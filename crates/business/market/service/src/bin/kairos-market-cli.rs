use std::path::PathBuf;

use clap::{Args, Parser, Subcommand, ValueEnum};
use kairos_integration::application::ProductFamily;
use kairos_market::composition::{
    binance_derivatives_rest_feed, binance_spot_rest_feed, binance_spot_websocket_feed,
    default_endpoint, ReplayMarketFeed,
};
use kairos_market::{MarketApplication, MarketDescriptor, MarketRuntime, SubscriptionId};
use kairos_workspace::cli::{render, OutputFormat};
use kairos_workspace::Workspace;
use serde_json::{json, Value};
use std::str::FromStr;

fn main() {
    if let Err(error) = run() {
        eprintln!("kairos-market-cli: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args = Cli::parse();
    let output = match args.output {
        Some(output) => output,
        None => args
            .workspace
            .as_ref()
            .map(Workspace::open)
            .transpose()?
            .map_or(OutputFormat::Json, |workspace| {
                workspace
                    .cli_format()
                    .parse()
                    .expect("workspace output format validated when opened")
            }),
    };
    let value = match args.command {
        Command::Validate(command) => validate(command)?,
        Command::Once(command) => once(command)?,
        Command::Replay(command) => replay(command)?,
    };
    println!("{}", render(&value, output));
    Ok(())
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
    let actor_id = command.actor_id;
    let endpoint = command
        .endpoint
        .unwrap_or_else(|| default_endpoint(command.provider.as_str()).to_string());
    let feed = match command.provider {
        Provider::BinanceSpotRest => binance_spot_rest_feed(endpoint)?,
        Provider::BinanceSpotWebsocket => binance_spot_websocket_feed(endpoint)?,
        Provider::BinanceOptionsRest => {
            binance_derivatives_rest_feed(ProductFamily::Options, endpoint, "/eapi/v1/ticker")?
        }
    };
    let application = MarketApplication::new(actor_id, 10_000)?;
    let mut runtime = MarketRuntime::with_feed(application, Box::new(feed));
    runtime.start_feed()?;
    runtime.subscribe_static(SubscriptionId::new("cli-once")?, "cli", market)?;
    runtime.reconcile_feed()?;
    runtime.poll_feed()?;
    Ok(serde_json::to_value(runtime.snapshot())?)
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
    let application = MarketApplication::new(command.actor_id, 10_000)?;
    let mut runtime =
        MarketRuntime::with_feed(application, Box::new(ReplayMarketFeed::new(events)));
    runtime.start_feed()?;
    runtime.subscribe_static(SubscriptionId::new("cli-replay")?, "cli", market)?;
    runtime.reconcile_feed()?;
    let count = runtime.poll_feed()?;
    Ok(json!({"events_applied": count, "snapshot": runtime.snapshot()}))
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

#[derive(Debug, Parser)]
#[command(name = "kairos-market-cli", about = "One-shot Market CLI")]
struct Cli {
    #[arg(long, global = true)]
    workspace: Option<PathBuf>,
    #[arg(long, value_parser = OutputFormat::from_str, global = true)]
    output: Option<OutputFormat>,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Validate(ValidateCommand),
    Once(OnceCommand),
    Replay(ReplayCommand),
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
    #[arg(long)]
    endpoint: Option<String>,
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
    BinanceOptionsRest,
}

impl Provider {
    fn as_str(self) -> &'static str {
        match self {
            Self::BinanceSpotRest => "binance-spot-rest",
            Self::BinanceSpotWebsocket => "binance-spot-websocket",
            Self::BinanceOptionsRest => "binance-options-rest",
        }
    }
}
