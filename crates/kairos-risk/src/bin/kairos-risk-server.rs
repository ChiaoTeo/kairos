use clap::Parser;
use std::time::Duration;

use kairos_risk::composition::JsonRiskStore;
use kairos_risk::{RiskApplication, RiskProcess};
use kairos_workspace::workspace::Workspace;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let workspace = Workspace::open(args.workspace)?;
    let instance = workspace.instance(&args.launch_mode, &args.launch_id, &args.instance_id)?;
    instance.prepare()?;
    let socket = instance.socket("risk")?;
    let health = instance.health("risk")?;
    let state = instance.state(&["risk", "risk-state.json"])?;
    let application = RiskApplication::with_dependencies(
        format!("risk:{}", args.instance_id),
        Vec::new(),
        false,
        Some(Box::new(JsonRiskStore::new(state))),
    )?;
    RiskProcess::new(
        application,
        socket,
        Duration::from_millis(args.interval_ms),
        Some(health),
    )?
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
}
