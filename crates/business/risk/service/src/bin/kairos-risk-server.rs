use clap::Parser;
use std::time::Duration;

use kairos_risk::composition::{
    compose_risk_application, AeronRiskEventPublisher, MmapRiskSnapshotPublisher,
};
use kairos_risk::RiskProcess;
use kairos_risk::{Amount, EnforcementMode, Metric, PolicyScope, RiskPolicy};
use kairos_workspace::workspace::Workspace;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    kairos_workspace::logging::init("risk");
    let result = run().await;
    if let Err(error) = &result {
        tracing::error!(event = "process_failed", component = "risk", error = %error, "risk server failed");
    }
    kairos_workspace::logging::shutdown();
    if let Err(error) = result {
        eprintln!("kairos-risk-server: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    tracing::info!(event = "process_start", component = "risk", instance_id = %args.instance_id, launch_id = %args.launch_id, "starting risk server");
    let workspace = Workspace::open(args.workspace)?;
    let instance = workspace.instance(&args.launch_mode, &args.launch_id, &args.instance_id)?;
    instance.prepare()?;
    let transport_identity = kairos_protocol::InstanceIdentity::new(
        workspace.id(),
        instance.launch_id(),
        instance.instance_id(),
    );
    let _process_lock = instance.process_lock("risk")?;
    let socket = instance.socket("risk")?;
    let health = instance.health("risk")?;
    let state = instance.state(&["risk", "risk-state.json"])?;
    let snapshot = instance.service_snapshot("risk")?;
    let policies = if args.launch_mode == "backtest" {
        vec![RiskPolicy {
            policy_id: kairos_domain_types::PolicyId::new("backtest-notional")?,
            version: 1.into(),
            scope: PolicyScope {
                account_id: None,
                strategy_id: None,
                instrument_id: None,
                exchange_id: None,
            },
            metric: Metric::Notional,
            // The policy is intentionally permissive but real: every replay
            // order still passes Risk authorization and creates a reservation.
            limit: Amount::new(1_000_000_000_000, 0)?,
            enforcement: EnforcementMode::Reject,
            valid_from_unix_nanos: 0.into(),
            valid_until_unix_nanos: None,
            window_nanos: None,
        }]
    } else {
        Vec::new()
    };
    let application =
        compose_risk_application(format!("risk:{}", args.instance_id), policies, Some(state))?;
    RiskProcess::new(
        application,
        socket,
        Duration::from_millis(args.interval_ms),
        Some(health),
    )?
    .with_replay_clock(args.launch_mode == "backtest")
    .with_snapshot_publisher(MmapRiskSnapshotPublisher::create(
        snapshot,
        1024 * 1024,
        format!("risk:{}", args.instance_id),
    )?)
    .with_event_publisher(AeronRiskEventPublisher::connect(
        args.aeron_dir.as_deref(),
        &args.aeron_channel,
        args.risk_events_stream_id,
        format!("risk:{}", args.instance_id),
        transport_identity,
    )?)
    .run()
    .await
}

#[derive(Debug, Parser)]
#[command(name = "kairos-risk", about = "Run the Risk actor process")]
struct Args {
    #[arg(long)]
    workspace: String,

    #[arg(long, visible_alias = "launch-mode", default_value = "paper")]
    launch_mode: String,

    #[arg(long)]
    launch_id: String,

    #[arg(long, default_value = "default")]
    instance_id: String,

    #[arg(long, default_value_t = 1_000, value_parser = clap::value_parser!(u64).range(1..))]
    interval_ms: u64,

    #[arg(long, env = "AERON_DIR")]
    aeron_dir: Option<String>,

    #[arg(long, default_value = kairos_transport::DEFAULT_CHANNEL)]
    aeron_channel: String,

    #[arg(
        long,
        default_value_t = kairos_transport::stream_ids::RISK_EVENTS
    )]
    risk_events_stream_id: i32,
}
