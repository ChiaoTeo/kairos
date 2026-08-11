use std::path::{Path, PathBuf};

use clap::{Args, Parser, Subcommand, ValueEnum};
use kairos_domain_types::{InstrumentId, MarketId};
use kairos_integration::application::HistoricalMarketRequest;
use kairos_integration::application::MarketEventKind;
use kairos_integration::blocking::HistoricalMarketDataConnection;
use kairos_integration::participants::binance;
use kairos_integration::participants::massive::{
    MarketType as MassiveMarketType, MassiveConnection, MassiveConnectionConfig,
};
use kairos_market::composition::{
    binance_derivatives_rest_feed, binance_spot_rest_feed, binance_spot_websocket_feed,
    default_endpoint, replay_market_feed, MarketProduct,
};
use kairos_market::{
    load_replay_events_many, MarketApplication, MarketDescriptor, MarketRuntime, SubscriptionId,
};
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
        Command::Download(command) => download(command, args.workspace.as_ref())?,
    };
    println!("{}", render(&value, output));
    Ok(())
}

fn download(
    command: DownloadCommand,
    workspace_root: Option<&PathBuf>,
) -> Result<Value, Box<dyn std::error::Error>> {
    let provider = command.provider.to_ascii_lowercase();
    if !matches!(provider.as_str(), "massive" | "binance") {
        return Err(format!("unsupported historical provider: {provider}").into());
    }
    let endpoint = command.endpoint.unwrap_or_else(|| {
        if provider == "massive" {
            "https://api.massive.com".into()
        } else {
            "https://data-api.binance.vision".into()
        }
    });
    let mut connection: Box<dyn HistoricalMarketDataConnection> = if provider == "massive" {
        let provider = MassiveConnection::connect(MassiveConnectionConfig {
            environment: "public".into(),
            rest_base_url: endpoint,
            api_key: secrecy::SecretString::new(
                command
                    .api_key
                    .clone()
                    .ok_or("Massive --api-key is required")?
                    .into(),
            ),
        })?;
        Box::new(provider.blocking_historical_market(MassiveMarketType::Equity)?)
    } else {
        binance::blocking::spot_historical_market(endpoint)?
    };
    let start_time_unix_nanos = millis_to_nanos(command.start)?;
    let end_time_unix_nanos = millis_to_nanos(command.end)?;
    let request = HistoricalMarketRequest {
        symbol: kairos_domain_types::Symbol::new(command.symbol.clone())
            .map_err(|error| error.to_string())?,
        data_kind: kairos_integration::application::MarketDataKind::Bar,
        start_time_unix_nanos,
        end_time_unix_nanos,
        interval: Some(command.interval.clone()),
        adjusted: Some(false),
    };
    let events = connection.fetch(&request)?;
    let symbol = command.symbol.to_ascii_uppercase();
    let market_id = command.market_id.unwrap_or_else(|| {
        if provider == "massive" {
            format!("market:massive:equity:{symbol}")
        } else {
            format!("market:binance:spot:{symbol}")
        }
    });
    let instrument_id = command.instrument_id.unwrap_or_else(|| {
        if provider == "massive" {
            format!("instrument:equity:US:{symbol}:common")
        } else {
            format!("instrument:spot:{symbol}")
        }
    });
    let output = command.file;
    if let Some(parent) = output
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)?;
    }
    let mut body = String::new();
    let mut count = 0usize;
    for event in events {
        if event.kind != MarketEventKind::Bar {
            continue;
        }
        let bar = event.bar.ok_or("historical bar payload is missing")?;
        let observation = kairos_market::MarketObservation::Bar(kairos_market::Bar {
            market_id: MarketId::new(&market_id)?,
            instrument_id: InstrumentId::new(&instrument_id)?,
            timeframe: bar.timeframe,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            volume: bar.volume,
            observed_at_unix_nanos: event.observed_at_unix_nanos,
            source_id: provider.clone(),
            derivation: bar.derivation,
        });
        body.push_str(&serde_json::to_string(&observation)?);
        body.push('\n');
        count += 1;
    }
    std::fs::write(&output, body)?;
    let manifest = serde_json::json!({
        "dataset_id": command.dataset_id,
        "source": provider,
        "symbol": command.symbol,
        "market_id": market_id,
        "instrument_id": instrument_id,
        "data_kind": "bar",
        "interval": command.interval,
        "start_time_unix_millis": command.start,
        "end_time_unix_millis": command.end,
        "event_count": count,
        "path": output,
        "format": "jsonl",
    });
    let manifest_path = output.with_extension("manifest.json");
    std::fs::write(&manifest_path, serde_json::to_vec_pretty(&manifest)?)?;
    if let Some(workspace_root) = workspace_root {
        register_dataset(workspace_root, &manifest, &output, &manifest_path)?;
    }
    Ok(manifest)
}

fn millis_to_nanos(value: i64) -> Result<kairos_domain_types::UnixNanos, String> {
    let value = u64::try_from(value).map_err(|_| "historical time must be non-negative")?;
    value
        .checked_mul(1_000_000)
        .map(kairos_domain_types::UnixNanos::new)
        .ok_or_else(|| "historical time is out of range".into())
}

fn register_dataset(
    workspace_root: &PathBuf,
    manifest: &Value,
    output: &Path,
    manifest_path: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let dataset_id = manifest
        .get("dataset_id")
        .and_then(Value::as_str)
        .ok_or("dataset manifest has no dataset_id")?;
    if dataset_id.is_empty()
        || dataset_id == "."
        || dataset_id == ".."
        || dataset_id.contains('/')
        || dataset_id.contains('\\')
    {
        return Err("dataset_id must be a single path-safe component".into());
    }
    let workspace = Workspace::open(workspace_root)?;
    let catalog_path = workspace.state_root().join("market").join("datasets.json");
    let Some(parent) = catalog_path.parent() else {
        return Err("dataset catalog has no parent directory".into());
    };
    std::fs::create_dir_all(parent)?;
    let mut catalog = if catalog_path.is_file() {
        serde_json::from_slice::<Value>(&std::fs::read(&catalog_path)?)?
    } else {
        json!({"datasets": [], "aliases": {}})
    };
    if !catalog.is_object() {
        return Err("dataset catalog must contain a JSON object".into());
    }
    let datasets = catalog
        .get_mut("datasets")
        .and_then(Value::as_array_mut)
        .ok_or("dataset catalog has no datasets array")?;
    datasets.retain(|item| item.get("name").and_then(Value::as_str) != Some(dataset_id));
    let output = output.canonicalize()?;
    let manifest_path = manifest_path.canonicalize()?;
    let mut entry = manifest.clone();
    let object = entry
        .as_object_mut()
        .ok_or("dataset manifest must be an object")?;
    object.insert("name".into(), json!(dataset_id));
    object.insert("path".into(), json!(output));
    object.insert("manifest_path".into(), json!(manifest_path));
    datasets.push(entry);
    datasets.sort_by(|left, right| {
        left.get("name")
            .and_then(Value::as_str)
            .cmp(&right.get("name").and_then(Value::as_str))
    });
    let temporary = catalog_path.with_extension("json.tmp");
    std::fs::write(&temporary, serde_json::to_vec_pretty(&catalog)?)?;
    std::fs::rename(temporary, catalog_path)?;
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
        command.market.exchange_id,
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
            binance_derivatives_rest_feed(MarketProduct::Options, endpoint, "/eapi/v1/ticker")?
        }
    };
    let application = MarketApplication::new(actor_id, 10_000)?;
    let mut runtime = MarketRuntime::with_feed(application, feed);
    runtime.start_feed()?;
    runtime.subscribe_static(SubscriptionId::new("cli-once")?, "cli", market)?;
    runtime.reconcile_feed()?;
    runtime.poll_feed()?;
    Ok(serde_json::to_value(runtime.snapshot())?)
}

fn replay(command: ReplayCommand) -> Result<Value, Box<dyn std::error::Error>> {
    let events = load_replay_events_many(command.files)?;
    let market = descriptor_from_values(
        command.market.market_id,
        command.market.instrument_id,
        command.market.exchange_id,
        command.market.market_type,
        command.market.source_symbol,
    )?;
    let application = MarketApplication::new(command.actor_id, 10_000)?;
    let mut runtime = MarketRuntime::with_feed(application, replay_market_feed(events));
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
        command.exchange_id.clone(),
        command.market_type.clone(),
        command.source_symbol.clone(),
    )
}

fn descriptor_from_values(
    market_id: String,
    instrument_id: String,
    exchange_id: String,
    market_type: String,
    source_symbol: String,
) -> Result<MarketDescriptor, String> {
    MarketDescriptor::new(
        market_id,
        instrument_id,
        exchange_id,
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
    Download(DownloadCommand),
}

#[derive(Debug, Args)]
struct DescriptorArgs {
    #[arg(long, default_value = "market:binance:spot:BTCUSDT")]
    market_id: String,
    #[arg(long, default_value = "instrument:spot:BTC")]
    instrument_id: String,
    #[arg(long, default_value = "binance")]
    exchange_id: String,
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
    #[arg(long = "file", required = true)]
    files: Vec<PathBuf>,
    #[arg(long, default_value = "market-cli-replay")]
    actor_id: String,
}

#[derive(Debug, Args)]
struct DownloadCommand {
    #[arg(long, default_value = "binance")]
    provider: String,
    #[arg(long)]
    api_key: Option<String>,
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long)]
    symbol: String,
    #[arg(long)]
    market_id: Option<String>,
    #[arg(long)]
    instrument_id: Option<String>,
    #[arg(long)]
    start: i64,
    #[arg(long)]
    end: i64,
    #[arg(long, default_value = "1m")]
    interval: String,
    #[arg(long, default_value = "market-history")]
    dataset_id: String,
    #[arg(long)]
    file: PathBuf,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
#[allow(clippy::enum_variant_names)]
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
