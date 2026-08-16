use std::path::{Path, PathBuf};

use clap::{Args, Parser, Subcommand, ValueEnum};
use kairos_primitives::{InstrumentId, MarketId};
use kairos_integration::application::credential::load_workspace_credential;
use kairos_integration::application::{
    AsyncHistoricalMarketDataConnection, HistoricalMarketRequest, MarketEventKind,
};
use kairos_integration::participants::binance;
use kairos_integration::participants::massive::{
    MarketType as MassiveMarketType, MassiveConnection, MassiveConnectionConfig,
};
use kairos_market::composition::{
    attach_binance_derivatives_source, attach_binance_spot_rest_source, attach_binance_spot_source,
    attach_replay_source, default_endpoint, MarketProduct,
};
use kairos_market::{load_replay_events_many, MarketApplication, MarketDescriptor, SubscriptionId};
use kairos_workspace::cli::{render, OutputFormat};
use kairos_workspace::Workspace;
use serde_json::{json, Value};
use std::str::FromStr;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("kairos-market-cli: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
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
        Command::Once(command) => once(command).await?,
        Command::Replay(command) => replay(command).await?,
        Command::Download(command) => download(command, args.workspace.as_ref()).await?,
    };
    println!("{}", render(&value, output));
    Ok(())
}

async fn download(
    command: DownloadCommand,
    workspace_root: Option<&PathBuf>,
) -> Result<Value, Box<dyn std::error::Error>> {
    let provider = command.provider;
    let workspace = workspace_root.map(Workspace::open).transpose()?;
    let configured_endpoint = workspace.as_ref().and_then(|workspace| {
        let reference: toml::Value = workspace.read_section("reference").ok()?;
        reference
            .get("providers")?
            .get(provider.as_str())?
            .get("endpoint")?
            .as_str()
            .map(str::to_owned)
    });
    let endpoint = command
        .endpoint
        .or(configured_endpoint)
        .unwrap_or_else(|| match provider {
            HistoricalProvider::Massive => "https://api.massive.com".into(),
            HistoricalProvider::Binance => "https://data-api.binance.vision".into(),
        });
    let start_time_unix_nanos = millis_to_nanos(command.start)?;
    let end_time_unix_nanos = millis_to_nanos(command.end)?;
    let data_kind = match command.data_kind {
        HistoricalDataKind::Bar => kairos_integration::application::MarketDataKind::Bar,
        HistoricalDataKind::Quote => kairos_integration::application::MarketDataKind::Quote,
        HistoricalDataKind::Trade => kairos_integration::application::MarketDataKind::Trade,
    };
    let request = HistoricalMarketRequest {
        symbol: kairos_primitives::Symbol::new(command.symbol.clone())
            .map_err(|error| error.to_string())?,
        data_kind,
        start_time_unix_nanos,
        end_time_unix_nanos,
        interval: Some(command.interval.clone()),
        adjusted: Some(command.adjusted),
    };
    let events = match provider {
        HistoricalProvider::Massive => {
            let api_key = if let Some(value) = command.api_key.clone() {
                value
            } else {
                workspace_root
                    .ok_or("Massive download requires --workspace or the deprecated --api-key")?;
                let workspace = workspace
                    .as_ref()
                    .ok_or("Massive download workspace could not be opened")?;
                let credentials_root = workspace.child(&["credentials"])?;
                load_workspace_credential(
                    &credentials_root,
                    "massive",
                    command.credential_id.as_deref(),
                )?
                .ok_or("Massive workspace credential does not exist")?
                .api_key
            };
            let provider = MassiveConnection::connect(MassiveConnectionConfig {
                environment: "public".into(),
                rest_base_url: endpoint,
                api_key: secrecy::SecretString::new(api_key.into()),
            })?;
            provider
                .historical_market(match command.market_type {
                    HistoricalMarketType::Equity => MassiveMarketType::Equity,
                    HistoricalMarketType::Option => MassiveMarketType::Option,
                })?
                .fetch(&request)
                .await?
        }
        HistoricalProvider::Binance => {
            binance::spot_historical_market(endpoint)?
                .fetch(&request)
                .await?
        }
    };
    let market_id = command
        .market_id
        .ok_or("historical download requires Reference-owned --market-id")?;
    let instrument_id = command
        .instrument_id
        .ok_or("historical download requires Reference-owned --instrument-id")?;
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
        let observation = match event.kind {
            MarketEventKind::Bar => {
                let bar = event.bar.ok_or("historical bar payload is missing")?;
                kairos_market::MarketObservation::Bar(kairos_market::Bar {
                    market_id: MarketId::new(&market_id)?,
                    instrument_id: InstrumentId::new(&instrument_id)?,
                    timeframe: bar.timeframe,
                    open: bar.open,
                    high: bar.high,
                    low: bar.low,
                    close: bar.close,
                    volume: bar.volume,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: provider.as_str().into(),
                    derivation: bar.derivation,
                })
            }
            MarketEventKind::Quote => {
                kairos_market::MarketObservation::Quote(kairos_market::Quote {
                    market_id: MarketId::new(&market_id)?,
                    instrument_id: InstrumentId::new(&instrument_id)?,
                    bid_price: event.price,
                    bid_quantity: event.quantity,
                    ask_price: event.ask_price,
                    ask_quantity: event.ask_quantity,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: provider.as_str().into(),
                })
            }
            MarketEventKind::Trade => {
                kairos_market::MarketObservation::Trade(kairos_market::Trade {
                    market_id: MarketId::new(&market_id)?,
                    instrument_id: InstrumentId::new(&instrument_id)?,
                    trade_id: event
                        .sequence
                        .map(|value| format!("massive:{}", value.get())),
                    price: event.price.ok_or("historical trade price is missing")?,
                    quantity: event
                        .quantity
                        .ok_or("historical trade quantity is missing")?,
                    cost: None,
                    aggressor_side: None,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: provider.as_str().into(),
                })
            }
            _ => continue,
        };
        body.push_str(&serde_json::to_string(&observation)?);
        body.push('\n');
        count += 1;
    }
    std::fs::write(&output, body)?;
    let manifest = serde_json::json!({
        "dataset_id": command.dataset_id,
        "provider": provider.as_str(),
        "source": provider.as_str(),
        "symbol": command.symbol,
        "market_id": market_id,
        "instrument_id": instrument_id,
        "data_kind": command.data_kind.as_str(),
        "observation_type": command.data_kind.as_str(),
        "market_type": command.market_type.as_str(),
        "interval": command.interval,
        "timeframe": command.interval,
        "adjusted": command.adjusted,
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

fn millis_to_nanos(value: i64) -> Result<kairos_primitives::UnixNanos, String> {
    let value = u64::try_from(value).map_err(|_| "historical time must be non-negative")?;
    value
        .checked_mul(1_000_000)
        .map(kairos_primitives::UnixNanos::new)
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

async fn once(command: OnceCommand) -> Result<Value, Box<dyn std::error::Error>> {
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
    let mut runtime = MarketApplication::new(actor_id, 10_000)?;
    match command.provider {
        Provider::BinanceSpotRest => attach_binance_spot_rest_source(&mut runtime, endpoint)?,
        Provider::BinanceSpotWebsocket => attach_binance_spot_source(&mut runtime, endpoint)?,
        Provider::BinanceOptionsRest => attach_binance_derivatives_source(
            &mut runtime,
            MarketProduct::Options,
            endpoint,
            "/eapi/v1/ticker",
        )?,
    }
    runtime.subscribe_static(SubscriptionId::new("cli-once")?, "cli", market)?;
    runtime.sync_source_subscriptions().await?;
    while runtime.drive_next_source_input().await? == 0 {}
    Ok(serde_json::to_value(runtime.snapshot())?)
}

async fn replay(command: ReplayCommand) -> Result<Value, Box<dyn std::error::Error>> {
    let events = load_replay_events_many(command.files)?;
    let market = descriptor_from_values(
        command.market.market_id,
        command.market.instrument_id,
        command.market.exchange_id,
        command.market.market_type,
        command.market.source_symbol,
    )?;
    let mut runtime = MarketApplication::new(command.actor_id, 10_000)?;
    attach_replay_source(&mut runtime, events)?;
    runtime.subscribe_static(SubscriptionId::new("cli-replay")?, "cli", market)?;
    runtime.sync_source_subscriptions().await?;
    let mut count = 0;
    while !runtime.sources_complete() {
        count += runtime.drive_next_source_input().await?;
    }
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
    provider: HistoricalProvider,
    #[arg(long)]
    api_key: Option<String>,
    #[arg(long, default_value = "massive-readonly")]
    credential_id: Option<String>,
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long)]
    symbol: String,
    #[arg(long, value_enum, default_value_t = HistoricalMarketType::Equity)]
    market_type: HistoricalMarketType,
    #[arg(long, value_enum, default_value_t = HistoricalDataKind::Bar)]
    data_kind: HistoricalDataKind,
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
    #[arg(long, default_value_t = false)]
    adjusted: bool,
    #[arg(long, default_value = "market-history")]
    dataset_id: String,
    #[arg(long)]
    file: PathBuf,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum HistoricalProvider {
    Binance,
    Massive,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum HistoricalMarketType {
    Equity,
    Option,
}

impl HistoricalMarketType {
    fn as_str(self) -> &'static str {
        match self {
            Self::Equity => "equity",
            Self::Option => "option",
        }
    }
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum HistoricalDataKind {
    Bar,
    Quote,
    Trade,
}

impl HistoricalDataKind {
    fn as_str(self) -> &'static str {
        match self {
            Self::Bar => "bar",
            Self::Quote => "quote",
            Self::Trade => "trade",
        }
    }
}

impl HistoricalProvider {
    fn as_str(self) -> &'static str {
        match self {
            Self::Binance => "binance",
            Self::Massive => "massive",
        }
    }
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
