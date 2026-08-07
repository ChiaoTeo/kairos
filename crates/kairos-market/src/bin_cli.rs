use std::path::{Path, PathBuf};

use clap::{Args, Parser, Subcommand, ValueEnum};
use kairos_market::{
    binance_spot_rest_feed, binance_spot_websocket_feed, MarketActor, MarketApplication,
    MarketDescriptor, ReplayMarketFeed, SubscriptionId,
};
use kairos_workspace::{control::RestControlClient, workspace::Workspace};
use serde::Serialize;
use serde_json::{json, Value};

#[tokio::main(flavor = "current_thread")]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("kairos-market-cli: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Cli::parse();
    let value = match args.command {
        Command::Validate(command) => validate(command)?,
        Command::Once(command) => once(command)?,
        Command::Replay(command) => replay(command)?,
        Command::Status => remote(&args, "GET", "/v1/health", None).await?,
        Command::Snapshot => remote(&args, "GET", "/v1/snapshot", None).await?,
        Command::Subscribe(ref command) => {
            let body = serde_json::to_vec(&json!({
                "request_id": command.request_id,
                "strategy_id": command.strategy_id,
                "instance_id": command.instance_id,
                "subject": command.subject,
                "selectors": command.selectors,
                "exchange": command.exchange,
                "market_type": command.market_type,
                "identity": command.identity,
                "dynamic": command.dynamic,
            }))?;
            remote(&args, "POST", "/v1/subscribe", Some(body)).await?
        }
        Command::Unsubscribe(ref command) => {
            let body = serde_json::to_vec(&json!({
                "request_id": command.request_id,
                "subscription_id": command.subscription_id,
            }))?;
            remote(&args, "POST", "/v1/unsubscribe", Some(body)).await?
        }
        Command::Recover => remote(&args, "POST", "/v1/recover", None).await?,
        Command::Stop => remote(&args, "POST", "/v1/stop", None).await?,
    };
    print_value(&value, args.output);
    Ok(())
}

async fn remote(
    args: &Cli,
    method: &str,
    path: &str,
    body: Option<Vec<u8>>,
) -> Result<Value, Box<dyn std::error::Error>> {
    let workspace = required_workspace(args.workspace.as_deref())?;
    let client = RestControlClient::new(workspace.process_socket("market")?);
    Ok(client.request_json(method, path, body.as_deref()).await?)
}

fn required_workspace(path: Option<&Path>) -> Result<Workspace, Box<dyn std::error::Error>> {
    let path = path.ok_or("--workspace is required for a server command")?;
    Ok(Workspace::open(path)?)
}

fn validate(command: ValidateCommand) -> Result<Value, Box<dyn std::error::Error>> {
    let descriptor = descriptor(&command.market)?;
    descriptor.validate()?;
    Ok(json!({
        "valid": true,
        "market": descriptor,
    }))
}

fn once(command: OnceCommand) -> Result<Value, Box<dyn std::error::Error>> {
    let market = descriptor_from_values(
        command.market.market_id,
        command.market.instrument_id,
        command.market.venue_id,
        command.market.market_type,
        command.market.source_symbol,
    )?;
    let actor = MarketActor::new(command.actor_id, 10_000)?;
    let mut application = MarketApplication::new(actor);
    let feed = match command.provider {
        Provider::BinanceSpotRest => binance_spot_rest_feed(command.endpoint)?,
        Provider::BinanceSpotWebsocket => binance_spot_websocket_feed(command.endpoint)?,
    };
    application.attach_feed(Box::new(feed));
    application.subscribe_static(SubscriptionId::new("cli-once")?, "cli", market)?;
    application.poll_feed()?;
    Ok(serde_json::to_value(application.snapshot())?)
}

fn replay(command: ReplayCommand) -> Result<Value, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string(command.file)?;
    let events = content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(serde_json::from_str)
        .collect::<Result<Vec<kairos_market::MarketObservation>, _>>()?;
    let market = descriptor_from_values(
        command.market.market_id,
        command.market.instrument_id,
        command.market.venue_id,
        command.market.market_type,
        command.market.source_symbol,
    )?;
    let actor = MarketActor::new(command.actor_id, 10_000)?;
    let mut application = MarketApplication::new(actor);
    application.attach_feed(Box::new(ReplayMarketFeed::new(events)));
    application.subscribe_static(SubscriptionId::new("cli-replay")?, "cli", market)?;
    let count = application.poll_feed()?;
    Ok(json!({"events_applied": count, "snapshot": application.snapshot()}))
}

fn descriptor(command: &DescriptorArgs) -> Result<MarketDescriptor, String> {
    descriptor_from_values(
        command.market_id.clone(),
        command.instrument_id.clone(),
        command.venue_id.clone(),
        command.market_type.clone(),
        command.source_symbol.clone(),
    )
}

fn descriptor_from_values(
    market_id: String,
    instrument_id: String,
    venue_id: String,
    market_type: String,
    source_symbol: String,
) -> Result<MarketDescriptor, String> {
    MarketDescriptor::new(
        market_id,
        instrument_id,
        venue_id,
        market_type,
        source_symbol,
    )
}

fn print_value(value: &Value, output: OutputFormat) {
    match output {
        OutputFormat::Json => println!("{}", serde_json::to_string_pretty(value).unwrap()),
        OutputFormat::Text => print_text(value, ""),
    }
}

fn print_text(value: &Value, prefix: &str) {
    match value {
        Value::Object(values) => {
            for (key, value) in values {
                let name = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{prefix}.{key}")
                };
                print_text(value, &name);
            }
        }
        Value::Array(values) => {
            for (index, value) in values.iter().enumerate() {
                print_text(value, &format!("{prefix}[{index}]"));
            }
        }
        _ => println!("{prefix}: {value}"),
    }
}

#[derive(Debug, Parser)]
#[command(name = "kairos-market-cli", about = "One-shot Market CLI")]
struct Cli {
    #[arg(long, global = true)]
    workspace: Option<PathBuf>,
    #[arg(long, value_enum, default_value_t = OutputFormat::Json, global = true)]
    output: OutputFormat,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Validate(ValidateCommand),
    Once(OnceCommand),
    Replay(ReplayCommand),
    Status,
    Snapshot,
    Subscribe(SubscribeCommand),
    Unsubscribe(UnsubscribeCommand),
    Recover,
    Stop,
}

#[derive(Clone, Copy, Debug, Default, ValueEnum)]
enum OutputFormat {
    Text,
    #[default]
    Json,
}

#[derive(Debug, Args)]
struct DescriptorArgs {
    #[arg(long, default_value = "market:binance:spot:BTCUSDT")]
    market_id: String,
    #[arg(long, default_value = "instrument:binance:spot:BTCUSDT")]
    instrument_id: String,
    #[arg(long, default_value = "binance")]
    venue_id: String,
    #[arg(long, default_value = "spot")]
    market_type: String,
    #[arg(long, default_value = "BTCUSDT")]
    source_symbol: String,
}

#[derive(Debug, Args)]
struct ValidateCommand {
    #[command(flatten)]
    market: DescriptorArgs,
}

#[derive(Debug, Args)]
struct OnceCommand {
    #[command(flatten)]
    market: DescriptorArgs,
    #[arg(long, value_enum, default_value_t = Provider::BinanceSpotRest)]
    provider: Provider,
    #[arg(long, default_value = "https://api.binance.com")]
    endpoint: String,
    #[arg(long, default_value = "market-cli")]
    actor_id: String,
}

#[derive(Debug, Args)]
struct ReplayCommand {
    #[command(flatten)]
    market: DescriptorArgs,
    #[arg(long)]
    file: PathBuf,
    #[arg(long, default_value = "market-cli-replay")]
    actor_id: String,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum Provider {
    BinanceSpotRest,
    BinanceSpotWebsocket,
}

#[derive(Debug, Args, Serialize)]
struct SubscribeCommand {
    #[arg(long)]
    request_id: String,
    #[arg(long)]
    strategy_id: String,
    #[arg(long)]
    instance_id: String,
    #[arg(long)]
    subject: String,
    #[arg(long, value_delimiter = ',')]
    selectors: Vec<String>,
    #[arg(long)]
    exchange: Option<String>,
    #[arg(long)]
    market_type: Option<String>,
    #[arg(long)]
    identity: Option<String>,
    #[arg(long, default_value_t = false)]
    dynamic: bool,
}

#[derive(Debug, Args, Serialize)]
struct UnsubscribeCommand {
    #[arg(long)]
    request_id: String,
    #[arg(long)]
    subscription_id: String,
}
