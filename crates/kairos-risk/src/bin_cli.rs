use clap::{Parser, Subcommand};
use kairos_workspace::{control::RestControlClient, workspace::Workspace};

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let workspace = Workspace::open(args.workspace)?;
    let client = RestControlClient::new(workspace.process_socket("risk")?);
    match args.command {
        Command::Status => println!("{}", client.health().await?),
    }
    Ok(())
}

#[derive(Debug, Parser)]
#[command(name = "kairos-risk-cli", about = "Risk module CLI")]
struct Args {
    #[arg(long)]
    workspace: String,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Status,
}
