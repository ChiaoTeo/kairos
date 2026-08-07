use clap::Parser;
use std::time::Duration;

use kairos_risk::{RiskApplication, RiskProcess};
use kairos_workspace::workspace::Workspace;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let workspace = Workspace::open(args.workspace)?;
    let socket = workspace.process_socket("risk")?;
    let health = workspace.health_file("risk")?;
    let application =
        RiskApplication::with_dependencies("risk", Vec::new(), false, None, None, None)?;
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

    #[arg(long, default_value_t = 1_000, value_parser = clap::value_parser!(u64).range(1..))]
    interval_ms: u64,
}
