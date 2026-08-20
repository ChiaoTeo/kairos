use std::path::{Path, PathBuf};
use std::time::Duration;

use clap::Parser;
use kairos_conflux::{
    AeronOutputDeclaration, Conflux, ConfluxConfig, ConfluxSystem, HttpControlConfig,
};
use kairos_reference::ReferenceApplication;
use kairos_reference::composition::{
    ReferenceCompositionConfig, build_application, ensure_database_parent,
};
use kairos_reference_contract::{AeronEndpoint, ReferenceHttpControl};
use kairos_workspace::workspace::Workspace;
use tokio::task::LocalSet;

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    kairos_workspace::logging::init("reference");
    let result = LocalSet::new().run_until(run()).await;
    if let Err(error) = &result {
        tracing::error!(event = "process_failed", component = "reference", error = %error, "reference server failed");
    }
    kairos_workspace::logging::shutdown();
    if let Err(error) = result {
        eprintln!("kairos-reference-server: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let workspace = Workspace::open(args.workspace)?;
    validate_workspace_path(workspace.root(), args.socket.as_deref(), "socket")?;
    validate_workspace_path(workspace.root(), args.health_file.as_deref(), "health file")?;
    let _process_lock = workspace.process_lock("reference").map_err(|error| {
        if error.kind() == std::io::ErrorKind::AlreadyExists {
            "reference is already running for this workspace".to_string()
        } else {
            format!("acquire reference workspace lock: {error}")
        }
    })?;

    let database = workspace.child(&["state", "reference", "reference.sqlite"])?;
    ensure_database_parent(&database)?;
    let config = ReferenceCompositionConfig {
        workspace: Some(workspace.root().to_path_buf()),
        database,
        aeron_dir: args.aeron_dir.clone(),
        aeron_channel: args.aeron_channel.clone(),
        reference_changes_stream: args.reference_changes_stream,
    };

    if args.run_mode == "once" {
        return run_once(&config).await;
    }

    let socket = args
        .socket
        .unwrap_or(workspace.process_socket("reference")?);
    let health_file = args
        .health_file
        .or_else(|| workspace.health_file("reference").ok());
    let composition = build_application(&config, false).await?;
    let (mut application, system, _) = composition.into_conflux();
    application.configure_conflux(args.refresh_interval, true);

    let event_endpoint = AeronEndpoint::from_parts(
        config.aeron_dir.as_deref(),
        config.aeron_channel.clone(),
        config.reference_changes_stream,
    )?;
    let mut system = system;
    system.outputs().aeron.declare(
        "reference-changes".to_owned(),
        AeronOutputDeclaration {
            endpoint: event_endpoint,
            revision: 1,
        },
    )?;

    run_process(application, system, socket, health_file).await
}

async fn run_once(config: &ReferenceCompositionConfig) -> Result<(), Box<dyn std::error::Error>> {
    let mut composition = build_application(config, true).await?;
    composition.activate_sources().await?;
    let (mut application, mut system, event_writer) = composition.into_conflux();
    let mut writer = event_writer.ok_or("reference publication is not configured")?;
    let refresh = application
        .refresh_with_connections(&mut system.connections())
        .await?;
    loop {
        let publications = application.pending_publications(1_024).await?;
        if publications.is_empty() {
            break;
        }
        writer.publish(&publications)?;
        let event_ids = publications
            .iter()
            .map(|event| event.event_id().to_owned())
            .collect::<Vec<_>>();
        application.acknowledge_publications(&event_ids).await?;
    }
    println!(
        "reference generation={} event_sequence={} events={}",
        application.generation().get(),
        application.event_sequence().get(),
        refresh.events.len()
    );
    Ok(())
}

async fn run_process(
    application: ReferenceApplication,
    system: ConfluxSystem,
    socket: PathBuf,
    health_file: Option<PathBuf>,
) -> Result<(), Box<dyn std::error::Error>> {
    let (conflux, handle) = Conflux::new(
        application,
        system,
        ConfluxConfig {
            ingress_capacity: 256,
            ..ConfluxConfig::default()
        },
    )?;
    let outcome = conflux
        .with_http_control(
            handle,
            ReferenceHttpControl,
            HttpControlConfig::uds(socket).with_health_file(health_file),
        )
        .run()
        .await?;
    tracing::info!(
        event = "process_stopped",
        component = "reference",
        phase = ?outcome.phase,
        discarded_inputs = outcome.discarded_inputs,
        "Reference Conflux process stopped"
    );
    Ok(())
}

fn validate_workspace_path(
    workspace: &Path,
    path: Option<&Path>,
    label: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    if path.is_some_and(|path| !path.starts_with(workspace)) {
        return Err(format!("reference {label} must be inside workspace").into());
    }
    Ok(())
}

fn parse_refresh_interval(value: &str) -> Result<Duration, String> {
    let value = value.trim();
    let (number, multiplier) = if let Some(value) = value.strip_suffix('s') {
        (value, 1u64)
    } else if let Some(value) = value.strip_suffix('m') {
        (value, 60)
    } else if let Some(value) = value.strip_suffix('h') {
        (value, 60 * 60)
    } else {
        (value, 1)
    };
    let seconds = number
        .parse::<u64>()
        .ok()
        .and_then(|value| value.checked_mul(multiplier))
        .filter(|value| *value > 0)
        .ok_or_else(|| {
            "refresh interval must be a positive duration such as 30s, 5m, or 1h".to_string()
        })?;
    Ok(Duration::from_secs(seconds))
}

#[derive(Debug, Parser)]
#[command(
    name = "kairos-reference",
    about = "Refresh and publish Reference data"
)]
struct Args {
    #[arg(long)]
    workspace: PathBuf,
    #[arg(long)]
    socket: Option<PathBuf>,
    #[arg(long = "health-file")]
    health_file: Option<PathBuf>,
    #[arg(long = "aeron-channel", default_value = kairos_transport::DEFAULT_CHANNEL)]
    aeron_channel: String,
    #[arg(
        long = "reference-changes-stream",
        default_value_t = kairos_transport::stream_ids::REFERENCE_CHANGES,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    reference_changes_stream: i32,
    #[arg(long)]
    aeron_dir: Option<String>,
    #[arg(long = "refresh-interval", default_value = "5m", value_parser = parse_refresh_interval)]
    refresh_interval: Duration,
    #[arg(long = "run-mode", default_value = "daemon", value_parser = ["daemon", "once"])]
    run_mode: String,
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use clap::Parser;

    use super::{Args, parse_refresh_interval};

    #[test]
    fn refresh_interval_accepts_human_units_and_legacy_seconds() {
        assert_eq!(
            parse_refresh_interval("15m").unwrap(),
            Duration::from_secs(900)
        );
        assert_eq!(
            parse_refresh_interval("1h").unwrap(),
            Duration::from_secs(3600)
        );
        assert_eq!(
            parse_refresh_interval("30").unwrap(),
            Duration::from_secs(30)
        );
        assert!(parse_refresh_interval("0s").is_err());
    }

    #[test]
    fn canonical_transport_options_parse_and_old_name_is_rejected() {
        let canonical = Args::try_parse_from([
            "kairos-reference",
            "--workspace",
            "/tmp/workspace",
            "--aeron-channel",
            "aeron:ipc",
            "--reference-changes-stream",
            "1201",
        ])
        .unwrap();
        assert_eq!(canonical.aeron_channel, "aeron:ipc");
        assert_eq!(
            canonical.reference_changes_stream,
            kairos_transport::stream_ids::REFERENCE_CHANGES
        );
        assert!(
            Args::try_parse_from([
                "kairos-reference",
                "--workspace",
                "/tmp/workspace",
                "--channel",
                "aeron:ipc",
            ])
            .is_err()
        );
    }
}
