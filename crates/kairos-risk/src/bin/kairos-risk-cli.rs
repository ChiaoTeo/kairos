use clap::{Parser, Subcommand};
use kairos_risk::{RiskApplication, RiskSnapshot};
use kairos_workspace::Workspace;

/// One-shot Risk inspection commands.
///
/// Runtime control belongs to the System boundary and is performed through
/// the process-owned socket by a typed system client.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let _workspace = Workspace::open(&args.workspace)?;
    let application = RiskApplication::with_dependencies("risk-cli", Vec::new(), false, None)?;
    let value = match args.command {
        Command::Status => serde_json::json!({
            "status": "ready",
            "actor_id": application.snapshot().actor_id,
        }),
        Command::Snapshot => serde_json::to_value::<RiskSnapshot>(application.snapshot())?,
    };
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}

#[derive(Debug, Parser)]
#[command(name = "kairos-risk-cli", about = "One-shot Risk commands")]
struct Args {
    #[arg(long)]
    workspace: String,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Status,
    Snapshot,
}
