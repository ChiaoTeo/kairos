use std::path::PathBuf;

use clap::Parser;
use kairos_market::composition::{build_market_host, MarketHostRequest};

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    kairos_workspace::logging::init("market");
    let result = tokio::task::LocalSet::new().run_until(run()).await;
    if let Err(error) = &result {
        tracing::error!(event = "process_failed", component = "market", error = %error, "market server failed");
    }
    kairos_workspace::logging::shutdown();
    if let Err(error) = result {
        eprintln!("kairos-market-server: {error}");
        std::process::exit(1);
    }
}

#[allow(clippy::needless_question_mark)]
async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    tracing::info!(event = "process_start", component = "market", instance_id = %args.instance_id, runtime_profile = ?args.runtime_profile, "starting market server");
    build_market_host(MarketHostRequest {
        workspace: args.workspace,
        launch_mode: args.launch_mode,
        launch_id: args.launch_id,
        instance_id: args.instance_id,
        runtime_profile: args.runtime_profile,
    })
    .await?
    .run()
    .await
}

#[derive(Debug, Parser)]
#[command(name = "kairos-market", about = "Run the Market actor process")]
struct Args {
    #[arg(long)]
    workspace: PathBuf,
    #[arg(long, default_value = "paper")]
    launch_mode: String,
    #[arg(long)]
    launch_id: Option<String>,
    #[arg(long, default_value = "default")]
    instance_id: String,
    #[arg(long)]
    runtime_profile: Option<String>,
}
