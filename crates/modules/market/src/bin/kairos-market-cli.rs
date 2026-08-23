use std::collections::BTreeMap;
use std::path::PathBuf;
use std::str::FromStr;

use clap::{Args, Parser, Subcommand, ValueEnum};
use kairos_market::composition::default_endpoint;
use kairos_market::{
    CliMarketApplication, CliMarketDiagnosticProvider, CliMarketHistoricalDataKind,
    CliMarketHistoricalDownloadRequest, CliMarketHistoricalMarketType, CliMarketHistoricalProvider,
    ConnectedMarketApplication, ConnectedMarketSourceQuery, ConnectedSourceAvailability,
    MarketDataRoute, ResolvedMarket,
};
use kairos_market_contract::{MarketClient, MarketConnection};
use kairos_primitives::integration::ProviderId;
use kairos_primitives::market::{ObservationKind, SourceId, SubscriptionId};
use kairos_primitives::reference::{Exchange, InstrumentId, InstrumentKind, MarketId};
use kairos_primitives::runtime::{IdempotencyKey, InstanceId, RequestId, StrategyId};
use kairos_workspace::Workspace;
use kairos_workspace::cli::{OutputFormat, render};
use serde_json::Value;

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
        Command::Standalone(command) => run_standalone(command, args.workspace.as_ref()).await?,
        Command::Connected(command) => run_connected(command, args.workspace.as_ref()).await?,
    };
    println!("{}", render(&value, output));
    Ok(())
}

async fn run_connected(
    command: ConnectedCommand,
    workspace_root: Option<&PathBuf>,
) -> Result<Value, Box<dyn std::error::Error>> {
    match command {
        ConnectedCommand::Status(target) => {
            connected_market_app(target, workspace_root, false)?
                .health()
                .await
        },
        ConnectedCommand::Sources(command) => {
            connected_market_app(command.target.clone(), workspace_root, false)?
                .sources(command.into_query()?)
                .await
        },
        ConnectedCommand::Snapshot(command) => {
            let app = connected_market_app(command.target.clone(), workspace_root, true)?;
            if let Some(error) = validate_connected_source(
                &app,
                &command.market_id,
                &command.source_id,
                Some(command.kind.observation_kind()),
            )
            .await?
            {
                Ok(error)
            } else {
                read_connected_snapshot(&app, command)
            }
        },
        ConnectedCommand::Freshness(command) => {
            let app = connected_market_app(command.target.clone(), workspace_root, true)?;
            let observation_kind = command
                .qualifier
                .as_deref()
                .and_then(|value| ObservationKind::parse_selector(value).ok());
            if let Some(error) = validate_connected_source(
                &app,
                &command.market_id,
                &command.source_id,
                observation_kind,
            )
            .await?
            {
                Ok(error)
            } else {
                app.freshness_snapshot(command.market_id, command.source_id, command.qualifier)
            }
        },
        ConnectedCommand::Subscribe(command) => {
            let app = connected_market_app(command.target.clone(), workspace_root, false)?;
            app.subscribe(command.into_envelope()?).await
        },
        ConnectedCommand::Unsubscribe(command) => {
            let app = connected_market_app(command.target.clone(), workspace_root, false)?;
            app.unsubscribe(command.into_envelope()?).await
        },
        ConnectedCommand::Recover(target) => {
            connected_market_app(target, workspace_root, false)?
                .recover()
                .await
        },
        ConnectedCommand::PauseReplay(target) => {
            connected_market_app(target, workspace_root, false)?
                .pause_replay()
                .await
        },
        ConnectedCommand::ResumeReplay(target) => {
            connected_market_app(target, workspace_root, false)?
                .resume_replay()
                .await
        },
    }
}

async fn validate_connected_source(
    app: &ConnectedMarketApplication,
    market_id: &str,
    source_id: &str,
    observation_kind: Option<ObservationKind>,
) -> Result<Option<Value>, Box<dyn std::error::Error>> {
    let source_id = SourceId::new(source_id)?;
    let kind = observation_kind.map_or("requested view", ObservationKind::as_str);
    Ok(
        match app
            .source_availability(MarketId::new(market_id)?, &source_id, observation_kind)
            .await?
        {
            ConnectedSourceAvailability::Available => None,
            ConnectedSourceAvailability::NotReady => Some(source_not_ready_json(
                market_id,
                &source_id,
                observation_kind,
                kind,
            )),
            ConnectedSourceAvailability::NotAvailable => Some(source_not_available_json(
                market_id,
                &source_id,
                observation_kind,
                kind,
            )),
        },
    )
}

fn source_not_ready_json(
    market_id: &str,
    source_id: &SourceId,
    observation_kind: Option<ObservationKind>,
    kind_label: &str,
) -> Value {
    serde_json::json!({
        "kind": kind_label,
        "market_id": market_id,
        "source_id": source_id,
        "status": "unavailable",
        "present": false,
        "value": Value::Null,
        "error": {
            "code": "source_not_ready",
            "message": format!(
                "configured source {source_id} is not ready for Market {market_id}"
            ),
            "retryable": true,
            "details": {
                "market_id": market_id,
                "source_id": source_id,
                "observation_kind": observation_kind.map(ObservationKind::as_str),
                "next_action": "inspect connected sources and wait for source readiness",
            }
        }
    })
}

fn source_not_available_json(
    market_id: &str,
    source_id: &SourceId,
    observation_kind: Option<ObservationKind>,
    kind_label: &str,
) -> Value {
    serde_json::json!({
        "kind": kind_label,
        "market_id": market_id,
        "source_id": source_id,
        "status": "unavailable",
        "present": false,
        "value": Value::Null,
        "error": {
            "code": "source_not_available",
            "message": format!(
                "Market {market_id} has no configured source {source_id} supporting {kind_label}"
            ),
            "retryable": false,
            "details": {
                "market_id": market_id,
                "source_id": source_id,
                "observation_kind": observation_kind.map(ObservationKind::as_str),
                "next_action": "list connected sources before reading a view",
            }
        }
    })
}

fn connected_market_app(
    target: ConnectedTargetArgs,
    workspace_root: Option<&PathBuf>,
    require_views: bool,
) -> Result<ConnectedMarketApplication, Box<dyn std::error::Error>> {
    let socket = match target.socket {
        Some(socket) => socket,
        None => {
            let workspace_root = workspace_root.ok_or(
                "connected mode requires --socket or --workspace to select a Market server",
            )?;
            Workspace::open(workspace_root)?.process_socket("market")?
        },
    };
    let connection = MarketConnection::control_only(socket);
    let connection = if require_views {
        match target.view_root {
            Some(view_root) => connection.with_view_root(view_root),
            None => {
                let workspace_root = workspace_root
                    .ok_or("connected projection reads require --view-root or --workspace")?;
                connection.with_view_root(Workspace::open(workspace_root)?.root().join("snapshots"))
            },
        }
    } else {
        connection
    };
    Ok(ConnectedMarketApplication::connect(MarketClient::connect(
        connection,
    )))
}

fn read_connected_snapshot(
    app: &ConnectedMarketApplication,
    command: SnapshotCommand,
) -> Result<Value, Box<dyn std::error::Error>> {
    match command.kind {
        SnapshotKind::Quote => app.quote_snapshot(command.market_id, command.source_id),
        SnapshotKind::Bar => {
            let timeframe = command
                .timeframe
                .ok_or("connected snapshot bar requires --timeframe")?;
            app.bar_snapshot(command.market_id, command.source_id, timeframe)
        },
        SnapshotKind::Greeks => app.greeks_snapshot(command.market_id, command.source_id),
    }
}

async fn run_standalone(
    command: StandaloneCommand,
    workspace_root: Option<&PathBuf>,
) -> Result<Value, Box<dyn std::error::Error>> {
    let application = CliMarketApplication::open(workspace_root.map(PathBuf::as_path));
    match command {
        StandaloneCommand::Validate(command) => {
            application.validate_market(descriptor(&command.market)?)
        },
        StandaloneCommand::ReferenceUniverse(command) => {
            application.reference_universe(command.instrument_kind()?, command.limit)
        },
        StandaloneCommand::Once(command) => {
            let market = descriptor(&command.market)?;
            let provider = command.provider.diagnostic_provider();
            let endpoint = command
                .endpoint
                .unwrap_or_else(|| default_endpoint(command.provider.as_str()).to_string());
            application
                .once(market, provider, endpoint, command.actor_id)
                .await
        },
        StandaloneCommand::Replay(command) => {
            application
                .replay(
                    descriptor(&command.market)?,
                    command.files,
                    command.actor_id,
                )
                .await
        },
        StandaloneCommand::Download(command) => {
            application
                .download_historical(command.into_request())
                .await
        },
        StandaloneCommand::Datasets => application.historical_datasets(),
    }
}

fn descriptor(command: &DescriptorArgs) -> Result<ResolvedMarket, String> {
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
) -> Result<ResolvedMarket, String> {
    let instrument_kind = match market_type.as_str() {
        "equity" => kairos_primitives::reference::InstrumentKind::Equity,
        "spot" => kairos_primitives::reference::InstrumentKind::Spot,
        "perpetual" | "swap" => kairos_primitives::reference::InstrumentKind::Perpetual,
        "future" | "futures" => kairos_primitives::reference::InstrumentKind::Future,
        "option" | "options" => kairos_primitives::reference::InstrumentKind::Option,
        "index" => kairos_primitives::reference::InstrumentKind::Index,
        _ => return Err(format!("unsupported market type {market_type}")),
    };
    let route = MarketDataRoute::new(
        format!("cli:{market_id}"),
        "cli",
        market_type,
        source_symbol,
    )?;
    ResolvedMarket::new(
        market_id,
        instrument_id,
        instrument_kind,
        exchange_id,
        route,
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
    #[command(name = "standalone", subcommand)]
    Standalone(StandaloneCommand),
    #[command(name = "connected", subcommand)]
    Connected(ConnectedCommand),
}

#[derive(Debug, Subcommand)]
enum StandaloneCommand {
    Validate(ValidateCommand),
    ReferenceUniverse(ReferenceUniverseCommand),
    Once(OnceCommand),
    Replay(ReplayCommand),
    Download(DownloadCommand),
    Datasets,
}

#[derive(Debug, Subcommand)]
enum ConnectedCommand {
    Status(ConnectedTargetArgs),
    Sources(SourcesCommand),
    Snapshot(SnapshotCommand),
    Freshness(FreshnessCommand),
    Subscribe(SubscribeCommand),
    Unsubscribe(UnsubscribeCommand),
    Recover(ConnectedTargetArgs),
    PauseReplay(ConnectedTargetArgs),
    ResumeReplay(ConnectedTargetArgs),
}

#[derive(Clone, Debug, Args)]
struct ConnectedTargetArgs {
    #[arg(long)]
    socket: Option<PathBuf>,
    #[arg(long)]
    view_root: Option<PathBuf>,
}

#[derive(Debug, Args)]
struct SourcesCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    market_id: Option<String>,
    #[arg(long)]
    instrument_id: Option<String>,
    #[arg(long)]
    observation_kind: Option<String>,
    #[arg(long)]
    provider_id: Option<String>,
    #[arg(long)]
    configured_only: bool,
    #[arg(long)]
    ready_only: bool,
}

impl SourcesCommand {
    fn into_query(self) -> Result<ConnectedMarketSourceQuery, Box<dyn std::error::Error>> {
        Ok(ConnectedMarketSourceQuery {
            market_id: self.market_id.map(MarketId::new).transpose()?,
            instrument_id: self.instrument_id.map(InstrumentId::new).transpose()?,
            observation_kind: self
                .observation_kind
                .as_deref()
                .map(ObservationKind::parse_selector)
                .transpose()?,
            provider_id: self.provider_id.map(ProviderId::new).transpose()?,
            configured_only: self.configured_only,
            ready_only: self.ready_only,
        })
    }
}

#[derive(Debug, Args)]
struct SnapshotCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(value_enum)]
    kind: SnapshotKind,
    #[arg(long)]
    market_id: String,
    #[arg(long)]
    source_id: String,
    #[arg(long)]
    timeframe: Option<String>,
}

#[derive(Debug, Args)]
struct FreshnessCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    market_id: String,
    #[arg(long)]
    source_id: String,
    #[arg(long)]
    qualifier: Option<String>,
}

#[derive(Debug, Args)]
struct SubscribeCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    subscription_id: String,
    #[arg(long)]
    subject: String,
    #[arg(long)]
    source_id: Option<String>,
    #[arg(long, default_value = "cli")]
    strategy_id: String,
    #[arg(long, default_value = "cli")]
    instance_id: String,
    #[arg(long = "selector")]
    selectors: Vec<String>,
    #[arg(long)]
    exchange: Option<String>,
    #[arg(long)]
    market_type: Option<String>,
    #[arg(long)]
    asset_type: Option<String>,
    #[arg(long = "param")]
    params: Vec<String>,
    #[arg(long)]
    dynamic: bool,
}

impl SubscribeCommand {
    fn into_envelope(
        self,
    ) -> Result<
        kairos_market_contract::MarketCommandEnvelope<
            kairos_market_contract::MarketSubscribePayload,
        >,
        Box<dyn std::error::Error>,
    > {
        let subscription_id = SubscriptionId::new(self.subscription_id)?;
        let command_id = RequestId::new(subscription_id.to_string())?;
        let idempotency_key = IdempotencyKey::new(subscription_id.to_string())?;
        Ok(kairos_market_contract::MarketCommandEnvelope {
            schema_version: 1,
            command_id,
            idempotency_key,
            operation: kairos_market_contract::MarketOperation::Subscribe,
            strategy_id: StrategyId::new(self.strategy_id)?,
            launch_id: None,
            instance_id: InstanceId::new(self.instance_id)?,
            payload: kairos_market_contract::MarketSubscribePayload {
                subject: self.subject,
                selectors: self.selectors,
                source_id: self.source_id.map(SourceId::new).transpose()?,
                source_ids: Vec::new(),
                exchange: self.exchange.map(Exchange::new).transpose()?,
                market_type: self.market_type.as_deref().map(str::parse).transpose()?,
                asset_type: self.asset_type.as_deref().map(str::parse).transpose()?,
                params: parse_params(self.params)?,
                dynamic: self.dynamic,
            },
        })
    }
}

#[derive(Debug, Args)]
struct UnsubscribeCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    subscription_id: String,
    #[arg(long, default_value = "cli")]
    strategy_id: String,
    #[arg(long, default_value = "cli")]
    instance_id: String,
}

impl UnsubscribeCommand {
    fn into_envelope(
        self,
    ) -> Result<
        kairos_market_contract::MarketCommandEnvelope<
            kairos_market_contract::MarketUnsubscribePayload,
        >,
        Box<dyn std::error::Error>,
    > {
        let subscription_id = SubscriptionId::new(self.subscription_id)?;
        Ok(kairos_market_contract::MarketCommandEnvelope {
            schema_version: 1,
            command_id: RequestId::new(format!("unsubscribe:{subscription_id}"))?,
            idempotency_key: IdempotencyKey::new(format!("unsubscribe:{subscription_id}"))?,
            operation: kairos_market_contract::MarketOperation::Unsubscribe,
            strategy_id: StrategyId::new(self.strategy_id)?,
            launch_id: None,
            instance_id: InstanceId::new(self.instance_id)?,
            payload: kairos_market_contract::MarketUnsubscribePayload { subscription_id },
        })
    }
}

fn parse_params(
    values: Vec<String>,
) -> Result<BTreeMap<String, serde_json::Value>, Box<dyn std::error::Error>> {
    let mut params = BTreeMap::new();
    for value in values {
        let (key, raw) = value.split_once('=').ok_or("--param must use KEY=VALUE")?;
        let parsed =
            serde_json::from_str(raw).unwrap_or_else(|_| serde_json::Value::String(raw.to_owned()));
        params.insert(key.to_owned(), parsed);
    }
    Ok(params)
}

#[derive(Clone, Debug, ValueEnum)]
enum SnapshotKind {
    Quote,
    Bar,
    Greeks,
}

impl SnapshotKind {
    const fn observation_kind(&self) -> ObservationKind {
        match self {
            Self::Quote => ObservationKind::Quote,
            Self::Bar => ObservationKind::Bar,
            Self::Greeks => ObservationKind::OptionGreeks,
        }
    }
}

#[derive(Debug, Args)]
struct ReferenceUniverseCommand {
    #[arg(long, default_value = "option")]
    instrument_kind: String,
    #[arg(long, default_value_t = 10_000)]
    limit: u64,
}

impl ReferenceUniverseCommand {
    fn instrument_kind(&self) -> Result<InstrumentKind, Box<dyn std::error::Error>> {
        let kind = match self.instrument_kind.as_str() {
            "equity" => InstrumentKind::Equity,
            "spot" => InstrumentKind::Spot,
            "perpetual" | "swap" => InstrumentKind::Perpetual,
            "future" | "futures" => InstrumentKind::Future,
            "option" | "options" => InstrumentKind::Option,
            "index" => InstrumentKind::Index,
            value => return Err(format!("unsupported instrument kind {value}").into()),
        };
        Ok(kind)
    }
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
    network_id: Option<String>,
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

impl DownloadCommand {
    fn into_request(self) -> CliMarketHistoricalDownloadRequest {
        CliMarketHistoricalDownloadRequest {
            provider: self.provider.into_application(),
            api_key: self.api_key,
            credential_id: self.credential_id,
            endpoint: self.endpoint,
            symbol: self.symbol,
            market_type: self.market_type.into_application(),
            data_kind: self.data_kind.into_application(),
            market_id: self.market_id,
            instrument_id: self.instrument_id,
            network_id: self.network_id,
            start_unix_millis: self.start,
            end_unix_millis: self.end,
            interval: self.interval,
            adjusted: self.adjusted,
            dataset_id: self.dataset_id,
            file: self.file,
        }
    }
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum HistoricalProvider {
    Binance,
    Massive,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum HistoricalMarketType {
    Spot,
    Equity,
    Option,
}

impl HistoricalMarketType {
    fn into_application(self) -> CliMarketHistoricalMarketType {
        match self {
            Self::Spot => CliMarketHistoricalMarketType::Spot,
            Self::Equity => CliMarketHistoricalMarketType::Equity,
            Self::Option => CliMarketHistoricalMarketType::Option,
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
    fn into_application(self) -> CliMarketHistoricalDataKind {
        match self {
            Self::Bar => CliMarketHistoricalDataKind::Bar,
            Self::Quote => CliMarketHistoricalDataKind::Quote,
            Self::Trade => CliMarketHistoricalDataKind::Trade,
        }
    }
}

impl HistoricalProvider {
    fn into_application(self) -> CliMarketHistoricalProvider {
        match self {
            Self::Binance => CliMarketHistoricalProvider::Binance,
            Self::Massive => CliMarketHistoricalProvider::Massive,
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

    fn diagnostic_provider(self) -> CliMarketDiagnosticProvider {
        match self {
            Self::BinanceSpotRest => CliMarketDiagnosticProvider::BinanceSpotRest,
            Self::BinanceSpotWebsocket => CliMarketDiagnosticProvider::BinanceSpotWebsocket,
            Self::BinanceOptionsRest => CliMarketDiagnosticProvider::BinanceOptionsRest,
        }
    }
}

#[cfg(test)]
mod tests {
    use clap::Parser;

    use super::*;

    #[test]
    fn connected_sources_maps_discovery_filters_to_typed_query() {
        let cli = Cli::try_parse_from([
            "kairos-market-cli",
            "connected",
            "sources",
            "--socket",
            "/tmp/market.sock",
            "--market-id",
            "market:binance:spot:BTCUSDT",
            "--instrument-id",
            "instrument:spot:BTC",
            "--observation-kind",
            "quote",
            "--provider-id",
            "binance",
            "--configured-only",
            "--ready-only",
        ])
        .unwrap();
        let Command::Connected(ConnectedCommand::Sources(command)) = cli.command else {
            panic!("expected connected sources command");
        };
        let query = command.into_query().unwrap();

        assert_eq!(
            query.market_id.as_ref().map(|value| value.as_str()),
            Some("market:binance:spot:BTCUSDT")
        );
        assert_eq!(query.observation_kind, Some(ObservationKind::Quote));
        assert_eq!(
            query.provider_id.as_ref().map(|value| value.as_str()),
            Some("binance")
        );
        assert!(query.configured_only);
        assert!(query.ready_only);
    }

    #[test]
    fn connected_sources_rejects_unknown_observation_kind() {
        let cli = Cli::try_parse_from([
            "kairos-market-cli",
            "connected",
            "sources",
            "--socket",
            "/tmp/market.sock",
            "--observation-kind",
            "mystery",
        ])
        .unwrap();
        let Command::Connected(ConnectedCommand::Sources(command)) = cli.command else {
            panic!("expected connected sources command");
        };

        assert!(command.into_query().is_err());
    }

    #[test]
    fn historical_download_accepts_spot_as_a_canonical_market_type() {
        let cli = Cli::try_parse_from([
            "kairos-market-cli",
            "standalone",
            "download",
            "--provider",
            "binance",
            "--symbol",
            "BTCUSDT",
            "--market-type",
            "spot",
            "--market-id",
            "market:binance:spot:BTCUSDT",
            "--instrument-id",
            "instrument:spot:BTCUSDT",
            "--start",
            "1",
            "--end",
            "2",
            "--file",
            "prices.jsonl",
        ])
        .unwrap();
        let Command::Standalone(StandaloneCommand::Download(command)) = cli.command else {
            panic!("expected standalone historical download command");
        };

        assert!(matches!(command.market_type, HistoricalMarketType::Spot));
    }

    #[test]
    fn historical_datasets_is_a_standalone_catalog_command() {
        let cli = Cli::try_parse_from(["kairos-market-cli", "standalone", "datasets"]).unwrap();

        assert!(matches!(
            cli.command,
            Command::Standalone(StandaloneCommand::Datasets)
        ));
    }

    #[test]
    fn unavailable_connected_source_returns_a_structured_result() {
        let value = source_not_available_json(
            "market:binance:spot:BTCUSDT",
            &SourceId::new("binance-spot").unwrap(),
            Some(ObservationKind::Quote),
            "quote",
        );

        assert_eq!(value["status"], "unavailable");
        assert_eq!(value["error"]["code"], "source_not_available");
        assert_eq!(value["error"]["retryable"], false);
        assert_eq!(
            value["error"]["details"]["next_action"],
            "list connected sources before reading a view"
        );
    }

    #[test]
    fn unready_connected_source_returns_a_retryable_structured_result() {
        let value = source_not_ready_json(
            "market:binance:spot:BTCUSDT",
            &SourceId::new("binance-spot").unwrap(),
            Some(ObservationKind::Quote),
            "quote",
        );

        assert_eq!(value["status"], "unavailable");
        assert_eq!(value["error"]["code"], "source_not_ready");
        assert_eq!(value["error"]["retryable"], true);
    }
}
