use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::time::Duration;

use clap::Parser;
use kairos_conflux::{Conflux, ConfluxConfig, ConfluxSystem, JsonRpcRuntimeConfig};
use kairos_reference::ReferenceApplication;
use kairos_reference::application::ReferenceRpcService;
use kairos_reference::composition::{
    ReferenceCompositionConfig, build_application, ensure_database_parent,
};
use kairos_reference::logging::events as log_events;
use kairos_reference_contract::ReferenceControlRpcServer;
use kairos_workspace::workspace::Workspace;
use tokio::task::LocalSet;

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    kairos_workspace::logging::init("reference");
    let result = LocalSet::new().run_until(run()).await;
    if let Err(error) = &result {
        let log_event = log_events::STARTUP_STAGE_FAILED;
        tracing::error!(
            event = log_event.event,
            component = log_event.component,
            area = log_event.area,
            action = log_event.action,
            outcome = log_event.outcome,
            legacy_event = "process_failed",
            error = %error,
            "reference server failed"
        );
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

    let health_file = args
        .health_file
        .or_else(|| workspace.health_file("reference").ok());
    let socket = args
        .socket
        .unwrap_or(workspace.process_socket("reference")?);
    let composition = build_application(&config, true).await?;
    let (mut application, system) = composition.into_conflux();
    application.configure_conflux(args.refresh_interval, true);

    run_process(application, system, args.rpc_address, socket, health_file).await
}

async fn run_process(
    application: ReferenceApplication,
    system: ConfluxSystem,
    rpc_address: SocketAddr,
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
    let invocation = handle.rpc_actor_invocation(Duration::from_secs(30));
    let methods = ReferenceRpcService::<ReferenceApplication>::new(invocation).into_rpc();
    let outcome = conflux
        .with_json_rpc(
            handle,
            methods,
            JsonRpcRuntimeConfig::tcp(rpc_address)
                .with_uds(socket)
                .with_health_file(health_file),
        )
        .run()
        .await?;
    let log_event = log_events::APP_PHASE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "process_stopped",
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
    #[arg(long = "rpc-address", default_value = "127.0.0.1:9474")]
    rpc_address: SocketAddr,
    #[arg(long)]
    socket: Option<PathBuf>,
    #[arg(long = "health-file")]
    health_file: Option<PathBuf>,
    #[arg(long = "aeron-channel", default_value = kairos_conflux::DEFAULT_AERON_CHANNEL)]
    aeron_channel: String,
    #[arg(
        long = "reference-changes-stream",
        default_value_t = kairos_conflux::output_stream_ids::REFERENCE_CHANGES,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    reference_changes_stream: i32,
    #[arg(long)]
    aeron_dir: Option<String>,
    #[arg(long = "refresh-interval", default_value = "5m", value_parser = parse_refresh_interval)]
    refresh_interval: Duration,
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
            kairos_conflux::output_stream_ids::REFERENCE_CHANGES
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
        assert!(
            Args::try_parse_from([
                "kairos-reference",
                "--workspace",
                "/tmp/workspace",
                "--run-mode",
                "once",
            ])
            .is_err()
        );
    }
}
