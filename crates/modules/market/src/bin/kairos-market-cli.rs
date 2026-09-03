use std::path::PathBuf;
use std::str::FromStr;

use chrono::{DateTime, SecondsFormat, Utc};
use clap::{Args, Parser, Subcommand, ValueEnum};
use kairos_market::composition::{
    CliReferenceUniverseResult, cli_reference_universe, compose_historical_market,
    compose_standalone_market, standalone_market_routes,
};
use kairos_market::{
    CliDirectObservationResult, CliMarketApplication, CliMarketDatasetManifest,
    CliMarketDatasetsResult, CliMarketHistoricalDataKind, CliMarketHistoricalDownloadRequest,
    CliMarketHistoricalMarketType, CliMarketHistoricalProvider, CliMarketOnceProvider,
    CliMarketOnceRequest, CliMarketReplayResult, CliMarketRoutesResult, CliMarketValidationResult,
    ConnectedMarketApplication, ConnectedMarketOutput, ConnectedMarketRouteQuery,
    ConnectedRouteAvailability, ResolvedMarket,
};
use kairos_market_contract::{MarketClient, MarketConnection};
use kairos_primitives::market::{ObservationKind, Provider, SubscriptionId};
use kairos_primitives::reference::{InstrumentId, InstrumentKind, MarketId};
use kairos_primitives::runtime::{
    IdempotencyKey, InstanceId, InstanceIdentity, RequestId, StrategyId,
};
use kairos_workspace::Workspace;
use kairos_workspace::cli::{OutputFormat, render, render_compact_table};
use serde::Serialize;
use serde_json::Value;

#[derive(Debug, Serialize)]
#[serde(untagged)]
enum MarketConnectedCliOutput {
    Typed(ConnectedMarketOutput),
    Diagnostic(Value),
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
enum MarketCliOutput {
    Standalone(MarketStandaloneCliOutput),
    Connected(MarketConnectedCliOutput),
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
enum MarketStandaloneCliOutput {
    Validation(CliMarketValidationResult),
    Routes(CliMarketRoutesResult),
    Snapshot(CliDirectObservationResult),
    ReferenceUniverse(CliReferenceUniverseResult),
    Replay(CliMarketReplayResult),
    Download(CliMarketDatasetManifest),
    Datasets(CliMarketDatasetsResult),
}

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
        Command::Standalone(command) => {
            MarketCliOutput::Standalone(run_standalone(command, args.workspace.as_ref()).await?)
        },
        Command::Connected(command) => {
            MarketCliOutput::Connected(run_connected(command, args.workspace.as_ref()).await?)
        },
    };
    println!("{}", render_market_cli_output(&value, output));
    Ok(())
}

fn render_market_cli_output(value: &MarketCliOutput, output: OutputFormat) -> String {
    if output == OutputFormat::Table {
        if let MarketCliOutput::Standalone(MarketStandaloneCliOutput::Snapshot(snapshot)) = value {
            return render_direct_snapshot(snapshot);
        }
    }
    render(value, output)
}

fn render_direct_snapshot(snapshot: &CliDirectObservationResult) -> String {
    match snapshot {
        CliDirectObservationResult::Quote(value) => {
            let mut rows = vec![
                vec!["标的".into(), value.symbol.clone()],
                vec!["Provider".into(), value.provider.clone()],
                vec![
                    "报价时间".into(),
                    format_unix_nanos(value.observed_at_unix_nanos),
                ],
                vec![
                    "卖一 Ask".into(),
                    format_level(value.ask_price, value.ask_quantity),
                ],
                vec![
                    "买一 Bid".into(),
                    format_level(value.bid_price, value.bid_quantity),
                ],
            ];
            if let (Some(ask), Some(bid)) = (value.ask_price, value.bid_price) {
                if let Ok(spread) = ask.checked_sub(bid) {
                    rows.push(vec!["价差".into(), spread.to_string()]);
                }
            }
            if let Some(last) = value.last_price {
                rows.push(vec!["最新价".into(), last.to_string()]);
            }
            render_compact_table(&["项目", "值"], &rows)
        },
        CliDirectObservationResult::Bar(value) => {
            let mut rows = vec![
                vec!["标的".into(), value.symbol.clone()],
                vec!["Provider".into(), value.provider.clone()],
                vec!["周期".into(), format_interval(&value.interval)],
                vec![
                    "K 线开始".into(),
                    format_unix_nanos(value.opened_at_unix_nanos),
                ],
            ];
            if let Some(closed_at) = value.closed_at_unix_nanos {
                rows.push(vec!["K 线结束".into(), format_unix_nanos(closed_at)]);
            }
            rows.extend([
                vec!["开盘".into(), value.open.to_string()],
                vec!["最高".into(), value.high.to_string()],
                vec!["最低".into(), value.low.to_string()],
                vec!["收盘".into(), value.close.to_string()],
                vec![
                    "成交量".into(),
                    value
                        .volume
                        .map_or_else(|| "—".into(), |item| item.to_string()),
                ],
            ]);
            if let Ok(change) = value.close.checked_sub(value.open) {
                rows.push(vec!["涨跌".into(), change.to_string()]);
            }
            if let Ok(range) = value.high.checked_sub(value.low) {
                rows.push(vec!["高低差".into(), range.to_string()]);
            }
            render_compact_table(&["项目", "值"], &rows)
        },
        _ => render(snapshot, OutputFormat::Table),
    }
}

fn format_level(
    price: Option<kairos_primitives::decimal::Price>,
    quantity: Option<kairos_primitives::decimal::Quantity>,
) -> String {
    match (price, quantity) {
        (Some(price), Some(quantity)) => format!("{price} × {quantity}"),
        (Some(price), None) => price.to_string(),
        (None, _) => "—".into(),
    }
}

fn format_unix_nanos(value: kairos_primitives::time::UnixNanos) -> String {
    let nanos = value.get();
    let seconds = i64::try_from(nanos / 1_000_000_000).ok();
    let subsecond_nanos = u32::try_from(nanos % 1_000_000_000).ok();
    seconds
        .zip(subsecond_nanos)
        .and_then(|(seconds, nanos)| DateTime::<Utc>::from_timestamp(seconds, nanos))
        .map(|value| value.to_rfc3339_opts(SecondsFormat::Millis, true))
        .unwrap_or_else(|| nanos.to_string())
}

fn format_interval(value: &str) -> String {
    match value {
        "1m" => "1 分钟".into(),
        "5m" => "5 分钟".into(),
        "15m" => "15 分钟".into(),
        "30m" => "30 分钟".into(),
        "1h" => "1 小时".into(),
        "1d" => "1 天".into(),
        _ => value.into(),
    }
}

async fn run_connected(
    command: ConnectedCommand,
    workspace_root: Option<&PathBuf>,
) -> Result<MarketConnectedCliOutput, Box<dyn std::error::Error>> {
    match command {
        ConnectedCommand::Status(target) => Ok(MarketConnectedCliOutput::Typed(
            ConnectedMarketOutput::Health(
                connected_market_app(target, workspace_root, false)?
                    .health()
                    .await?,
            ),
        )),
        ConnectedCommand::Routes(command) => Ok(MarketConnectedCliOutput::Typed(
            ConnectedMarketOutput::Routes(
                connected_market_app(command.target.clone(), workspace_root, false)?
                    .routes(command.into_query()?)
                    .await?,
            ),
        )),
        ConnectedCommand::Snapshot(command) => {
            let app = connected_market_app(command.target.clone(), workspace_root, true)?;
            let resolved = resolve_connected_provider(
                &app,
                &command.market_id,
                command.provider.as_deref(),
                Some(command.kind.observation_kind()),
            )
            .await?;
            match resolved {
                Ok(provider) => Ok(MarketConnectedCliOutput::Typed(read_connected_snapshot(
                    &app, &command, provider,
                )?)),
                Err(error) => Ok(MarketConnectedCliOutput::Diagnostic(error)),
            }
        },
        ConnectedCommand::Freshness(command) => {
            let app = connected_market_app(command.target.clone(), workspace_root, true)?;
            let observation_kind = command
                .observation
                .as_deref()
                .and_then(|value| ObservationKind::parse_selector(value).ok());
            let resolved = resolve_connected_provider(
                &app,
                &command.market_id,
                command.provider.as_deref(),
                observation_kind,
            )
            .await?;
            match resolved {
                Ok(provider) => Ok(MarketConnectedCliOutput::Typed(
                    ConnectedMarketOutput::Snapshot(app.freshness_snapshot(
                        command.market_id,
                        provider.to_string(),
                        command.observation,
                    )?),
                )),
                Err(error) => Ok(MarketConnectedCliOutput::Diagnostic(error)),
            }
        },
        ConnectedCommand::Subscribe(command) => {
            let app = connected_market_app(command.target.clone(), workspace_root, false)?;
            Ok(MarketConnectedCliOutput::Typed(
                ConnectedMarketOutput::Subscription(app.subscribe(command.into_envelope()?).await?),
            ))
        },
        ConnectedCommand::Unsubscribe(command) => {
            let app = connected_market_app(command.target.clone(), workspace_root, false)?;
            Ok(MarketConnectedCliOutput::Typed(
                ConnectedMarketOutput::Command(app.unsubscribe(command.into_envelope()?).await?),
            ))
        },
        ConnectedCommand::Recover(target) => Ok(MarketConnectedCliOutput::Typed(
            ConnectedMarketOutput::Command(
                connected_market_app(target, workspace_root, false)?
                    .recover()
                    .await?,
            ),
        )),
        ConnectedCommand::PauseReplay(target) => Ok(MarketConnectedCliOutput::Typed(
            ConnectedMarketOutput::Command(
                connected_market_app(target, workspace_root, false)?
                    .pause_replay()
                    .await?,
            ),
        )),
        ConnectedCommand::ResumeReplay(target) => Ok(MarketConnectedCliOutput::Typed(
            ConnectedMarketOutput::Command(
                connected_market_app(target, workspace_root, false)?
                    .resume_replay()
                    .await?,
            ),
        )),
    }
}

async fn validate_connected_route(
    app: &ConnectedMarketApplication,
    market_id: &str,
    provider: &str,
    observation_kind: Option<ObservationKind>,
) -> Result<Option<Value>, Box<dyn std::error::Error>> {
    let provider = Provider::new(provider)?;
    let kind = observation_kind.map_or("requested view", ObservationKind::as_str);
    Ok(
        match app
            .route_availability(MarketId::new(market_id)?, &provider, observation_kind)
            .await?
        {
            ConnectedRouteAvailability::Available => None,
            ConnectedRouteAvailability::NotReady => Some(route_not_ready_json(
                market_id,
                &provider,
                observation_kind,
                kind,
            )),
            ConnectedRouteAvailability::NotAvailable => Some(route_not_available_json(
                market_id,
                &provider,
                observation_kind,
                kind,
            )),
        },
    )
}

async fn resolve_connected_provider(
    app: &ConnectedMarketApplication,
    market_id: &str,
    requested: Option<&str>,
    observation_kind: Option<ObservationKind>,
) -> Result<Result<Provider, Value>, Box<dyn std::error::Error>> {
    if let Some(requested) = requested {
        let provider = Provider::new(requested)?;
        return Ok(
            match validate_connected_route(app, market_id, provider.as_str(), observation_kind)
                .await?
            {
                Some(error) => Err(error),
                None => Ok(provider),
            },
        );
    }

    let response = app
        .routes(ConnectedMarketRouteQuery {
            market_id: Some(MarketId::new(market_id)?),
            observation_kind,
            configured_only: true,
            ready_only: true,
            ..ConnectedMarketRouteQuery::default()
        })
        .await?;
    let mut providers = response
        .routes
        .into_iter()
        .map(|route| route.provider)
        .collect::<Vec<_>>();
    providers.sort();
    providers.dedup();
    Ok(match providers.as_slice() {
        [provider] => Ok(provider.clone()),
        [] => Err(serde_json::json!({
            "market_id": market_id,
            "status": "unavailable",
            "error": {
                "code": "no_ready_route",
                "message": format!("Market {market_id} has no ready provider route"),
                "retryable": true,
                "details": { "next_action": "inspect Market routes and provider readiness" }
            }
        })),
        _ => Err(serde_json::json!({
            "market_id": market_id,
            "status": "ambiguous",
            "providers": providers,
            "error": {
                "code": "provider_required",
                "message": "multiple ready provider routes exist; select --provider",
                "retryable": false
            }
        })),
    })
}

fn route_not_ready_json(
    market_id: &str,
    provider: &Provider,
    observation_kind: Option<ObservationKind>,
    kind_label: &str,
) -> Value {
    serde_json::json!({
        "kind": kind_label,
        "market_id": market_id,
        "provider": provider,
        "status": "unavailable",
        "present": false,
        "value": Value::Null,
        "error": {
            "code": "route_not_ready",
            "message": format!(
                "configured provider {provider} is not ready for Market {market_id}"
            ),
            "retryable": true,
            "details": {
                "market_id": market_id,
                "provider": provider,
                "observation_kind": observation_kind.map(ObservationKind::as_str),
                "next_action": "inspect Market routes and wait for provider readiness",
            }
        }
    })
}

fn route_not_available_json(
    market_id: &str,
    provider: &Provider,
    observation_kind: Option<ObservationKind>,
    kind_label: &str,
) -> Value {
    serde_json::json!({
        "kind": kind_label,
        "market_id": market_id,
        "provider": provider,
        "status": "unavailable",
        "present": false,
        "value": Value::Null,
        "error": {
            "code": "route_not_available",
            "message": format!(
                "Market {market_id} has no configured provider {provider} supporting {kind_label}"
            ),
            "retryable": false,
            "details": {
                "market_id": market_id,
                "provider": provider,
                "observation_kind": observation_kind.map(ObservationKind::as_str),
                "next_action": "list Market routes before reading a view",
            }
        }
    })
}

fn connected_market_app(
    target: ConnectedTargetArgs,
    workspace_root: Option<&PathBuf>,
    require_views: bool,
) -> Result<ConnectedMarketApplication, Box<dyn std::error::Error>> {
    let workspace_root = workspace_root.ok_or("connected mode requires --workspace")?;
    let workspace = Workspace::open(workspace_root)?;
    let instance = match (&target.launch_id, &target.instance_id) {
        (Some(launch_id), Some(instance_id)) => {
            Some(workspace.instance(&target.launch_mode, launch_id, instance_id)?)
        },
        (None, None) => None,
        _ => return Err("--launch-id and --instance-id must be provided together".into()),
    };
    let socket = match target.socket {
        Some(socket) => socket,
        None => match &instance {
            Some(instance) => instance.socket("market")?,
            None => workspace.process_socket("market")?,
        },
    };
    let connection = MarketConnection::control_only(socket);
    let connection = if require_views {
        match target.view_root {
            Some(view_root) => connection.with_view_root(view_root),
            None => connection.with_view_root(match &instance {
                Some(instance) => instance.snapshot(&[])?,
                None => workspace.paths().snapshots_root(),
            }),
        }
    } else {
        connection
    };
    let identity = match &instance {
        Some(instance) => {
            InstanceIdentity::new(workspace.id(), instance.launch_id(), instance.instance_id())?
        },
        None => InstanceIdentity::unscoped(workspace.id())?,
    };
    Ok(ConnectedMarketApplication::connect(
        MarketClient::connect(connection),
        identity,
    ))
}

fn read_connected_snapshot(
    app: &ConnectedMarketApplication,
    command: &SnapshotCommand,
    provider: Provider,
) -> Result<ConnectedMarketOutput, Box<dyn std::error::Error>> {
    match command.kind {
        SnapshotKind::Quote => Ok(ConnectedMarketOutput::Snapshot(
            app.quote_snapshot(command.market_id.clone(), provider.to_string())?,
        )),
        SnapshotKind::Bar => {
            let timeframe = command
                .timeframe
                .clone()
                .ok_or("connected snapshot bar requires --timeframe")?;
            Ok(ConnectedMarketOutput::Snapshot(app.bar_snapshot(
                command.market_id.clone(),
                provider.to_string(),
                timeframe,
            )?))
        },
        SnapshotKind::Greeks => Ok(ConnectedMarketOutput::Snapshot(
            app.greeks_snapshot(command.market_id.clone(), provider.to_string())?,
        )),
    }
}

async fn run_standalone(
    command: StandaloneCommand,
    workspace_root: Option<&PathBuf>,
) -> Result<MarketStandaloneCliOutput, Box<dyn std::error::Error>> {
    let open = || CliMarketApplication::open(workspace_root.map(PathBuf::as_path));
    match command {
        StandaloneCommand::Validate(command) => open()
            .validate_market(descriptor(&command.market)?)
            .map(MarketStandaloneCliOutput::Validation),
        StandaloneCommand::ReferenceUniverse(command) => {
            let workspace =
                Workspace::open(workspace_root.ok_or("reference-universe requires --workspace")?)?;
            cli_reference_universe(&workspace, command.instrument_kind()?, command.limit)
                .map(MarketStandaloneCliOutput::ReferenceUniverse)
        },
        StandaloneCommand::Routes(command) => {
            let sources = standalone_market_routes(
                workspace_root.map(PathBuf::as_path),
                &command.market_type,
                command.observation_kind.into_application(),
            )?;
            Ok(MarketStandaloneCliOutput::Routes(
                open().direct_routes(sources),
            ))
        },
        StandaloneCommand::Once(command) => {
            let provider = Provider::new(command.provider)?;
            let connection = direct_connection_for(provider.as_str(), &command.market.market_type)?;
            let request = CliMarketOnceRequest {
                provider,
                connection,
                symbol: command.market.symbol,
                observation_kind: command.observation_kind.into_application(),
                endpoint: command.endpoint,
                interval: command.interval,
                depth: command.depth,
                credential_id: command.credential_id,
            };
            let mut application =
                compose_standalone_market(workspace_root.map(PathBuf::as_path), &request)?;
            application
                .once(request)
                .await
                .map(MarketStandaloneCliOutput::Snapshot)
        },
        StandaloneCommand::Replay(command) => open()
            .replay(
                descriptor(&command.market)?,
                command.files,
                command.actor_id,
            )
            .await
            .map(MarketStandaloneCliOutput::Replay),
        StandaloneCommand::Download(command) => {
            let request = command.into_request();
            compose_historical_market(workspace_root.map(PathBuf::as_path), &request)?
                .download_historical(request)
                .await
                .map(MarketStandaloneCliOutput::Download)
        },
        StandaloneCommand::Datasets => open()
            .historical_datasets()
            .map(MarketStandaloneCliOutput::Datasets),
    }
}

fn descriptor(command: &DescriptorArgs) -> Result<ResolvedMarket, String> {
    descriptor_from_values(
        command.market_id.clone(),
        command.instrument_id.clone(),
        command.exchange_id.clone(),
        command.market_type.clone(),
    )
}

fn descriptor_from_values(
    market_id: String,
    instrument_id: String,
    exchange_id: String,
    market_type: String,
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
    ResolvedMarket::new(
        market_id,
        instrument_id,
        instrument_kind,
        exchange_id,
        "cli",
    )
    .map_err(|error| error.to_string())
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
    Routes(StandaloneRoutesCommand),
    Once(OnceCommand),
    Replay(ReplayCommand),
    Download(DownloadCommand),
    Datasets,
}

#[derive(Debug, Subcommand)]
enum ConnectedCommand {
    Status(ConnectedTargetArgs),
    Routes(RoutesCommand),
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
    #[arg(long, default_value = "live")]
    launch_mode: String,
    #[arg(long, requires = "instance_id")]
    launch_id: Option<String>,
    #[arg(long, requires = "launch_id")]
    instance_id: Option<String>,
    #[arg(long)]
    socket: Option<PathBuf>,
    #[arg(long)]
    view_root: Option<PathBuf>,
}

#[derive(Debug, Args)]
struct RoutesCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    market_id: Option<String>,
    #[arg(long)]
    instrument_id: Option<String>,
    #[arg(long)]
    observation_kind: Option<String>,
    #[arg(long)]
    provider: Option<String>,
    #[arg(long)]
    configured_only: bool,
    #[arg(long)]
    ready_only: bool,
}

impl RoutesCommand {
    fn into_query(self) -> Result<ConnectedMarketRouteQuery, Box<dyn std::error::Error>> {
        Ok(ConnectedMarketRouteQuery {
            market_id: self.market_id.map(MarketId::new).transpose()?,
            instrument_id: self.instrument_id.map(InstrumentId::new).transpose()?,
            observation_kind: self
                .observation_kind
                .as_deref()
                .map(ObservationKind::parse_selector)
                .transpose()?,
            provider: self.provider.map(Provider::new).transpose()?,
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
    provider: Option<String>,
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
    provider: Option<String>,
    #[arg(long)]
    observation: Option<String>,
}

#[derive(Debug, Args)]
struct SubscribeCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    subscription_id: String,
    #[arg(long)]
    market_id: String,
    #[arg(long, default_value = "cli")]
    strategy_id: String,
    #[arg(long, default_value = "cli")]
    instance_id: String,
    #[arg(long = "data", required = true)]
    data: Vec<String>,
    #[arg(long = "prefer-provider", conflicts_with_all = ["require_provider", "all_eligible_providers"])]
    prefer_provider: Vec<String>,
    #[arg(long = "require-provider", conflicts_with_all = ["prefer_provider", "all_eligible_providers"])]
    require_provider: Vec<String>,
    #[arg(long, conflicts_with_all = ["prefer_provider", "require_provider"])]
    all_eligible_providers: bool,
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
            schema_version: 2,
            command_id,
            idempotency_key,
            operation: kairos_market_contract::MarketOperation::Subscribe,
            strategy_id: StrategyId::new(self.strategy_id)?,
            launch_id: None,
            instance_id: InstanceId::new(self.instance_id)?,
            payload: kairos_market_contract::MarketSubscribePayload {
                target: kairos_market_contract::MarketTarget::Market {
                    market_id: MarketId::new(self.market_id)?,
                },
                observations: self
                    .data
                    .iter()
                    .map(|value| observation_requirement(value))
                    .collect::<Result<_, _>>()?,
                provider_preference: if self.all_eligible_providers {
                    kairos_market_contract::ProviderPreference::AllEligible
                } else if !self.require_provider.is_empty() {
                    kairos_market_contract::ProviderPreference::Require(
                        self.require_provider
                            .into_iter()
                            .map(Provider::new)
                            .collect::<Result<_, _>>()?,
                    )
                } else if !self.prefer_provider.is_empty() {
                    kairos_market_contract::ProviderPreference::Prefer(
                        self.prefer_provider
                            .into_iter()
                            .map(Provider::new)
                            .collect::<Result<_, _>>()?,
                    )
                } else {
                    kairos_market_contract::ProviderPreference::Automatic
                },
            },
        })
    }
}

fn observation_requirement(
    value: &str,
) -> Result<kairos_market_contract::ObservationRequirement, Box<dyn std::error::Error>> {
    let (kind, qualifier) = value.split_once(':').unwrap_or((value, ""));
    Ok(kairos_market_contract::ObservationRequirement {
        kind: ObservationKind::parse_selector(kind)?,
        qualifier: (!qualifier.is_empty()).then(|| qualifier.to_owned()),
    })
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
    /// User-facing market symbol. Provider-specific translation stays inside Market composition.
    #[arg(long, default_value = "BTCUSDT")]
    symbol: String,
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
    #[arg(long, default_value = "binance")]
    provider: String,
    #[arg(long, value_enum, default_value_t = OnceObservationKind::Quote)]
    observation_kind: OnceObservationKind,
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long, default_value = "1m")]
    interval: String,
    #[arg(long, default_value_t = 10)]
    depth: u32,
    #[arg(long)]
    credential_id: Option<String>,
}

#[derive(Debug, Args)]
struct StandaloneRoutesCommand {
    #[arg(long)]
    market_type: String,
    #[arg(long, value_enum)]
    observation_kind: OnceObservationKind,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum OnceObservationKind {
    Quote,
    Trade,
    Bar,
    OrderBook,
    OptionGreeks,
}

impl OnceObservationKind {
    const fn into_application(self) -> ObservationKind {
        match self {
            Self::Quote => ObservationKind::Quote,
            Self::Trade => ObservationKind::Trade,
            Self::Bar => ObservationKind::Bar,
            Self::OrderBook => ObservationKind::OrderBook,
            Self::OptionGreeks => ObservationKind::OptionGreeks,
        }
    }
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

fn direct_connection_for(
    provider: &str,
    market_type: &str,
) -> Result<CliMarketOnceProvider, Box<dyn std::error::Error>> {
    match (provider, market_type) {
        ("binance", "spot") => Ok(CliMarketOnceProvider::BinanceSpotRest),
        ("binance", "perpetual") => Ok(CliMarketOnceProvider::BinanceUsdMRest),
        ("binance", "equity") => Ok(CliMarketOnceProvider::BinanceEquityRest),
        ("binance", "option" | "options") => Ok(CliMarketOnceProvider::BinanceOptionsRest),
        ("massive", "equity") => Ok(CliMarketOnceProvider::MassiveRest),
        _ => Err(
            format!("provider {provider} has no direct {market_type} Market snapshot route").into(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use clap::Parser;
    use kairos_market::{CliMarketBarResult, CliMarketQuoteResult};

    use super::*;

    #[test]
    fn standalone_once_accepts_an_explicit_observation_kind() {
        let cli = Cli::try_parse_from([
            "kairos-market-cli",
            "standalone",
            "once",
            "--observation-kind",
            "order-book",
        ])
        .unwrap();
        let Command::Standalone(StandaloneCommand::Once(command)) = cli.command else {
            panic!("expected standalone once command");
        };

        assert_eq!(
            command.observation_kind.into_application(),
            ObservationKind::OrderBook
        );
    }

    #[test]
    fn standalone_once_accepts_a_provider_connection_choice() {
        let cli = Cli::try_parse_from([
            "kairos-market-cli",
            "standalone",
            "once",
            "--provider",
            "binance",
        ])
        .unwrap();
        let Command::Standalone(StandaloneCommand::Once(command)) = cli.command else {
            panic!("expected standalone once command");
        };

        assert_eq!(command.provider, "binance");
    }

    #[test]
    fn connected_routes_maps_discovery_filters_to_typed_query() {
        let cli = Cli::try_parse_from([
            "kairos-market-cli",
            "connected",
            "routes",
            "--launch-id",
            "launch",
            "--instance-id",
            "instance",
            "--socket",
            "/tmp/market.sock",
            "--market-id",
            "market:binance:spot:BTCUSDT",
            "--instrument-id",
            "instrument:spot:BTC",
            "--observation-kind",
            "quote",
            "--provider",
            "binance",
            "--configured-only",
            "--ready-only",
        ])
        .unwrap();
        let Command::Connected(ConnectedCommand::Routes(command)) = cli.command else {
            panic!("expected connected routes command");
        };
        let query = command.into_query().unwrap();

        assert_eq!(
            query.market_id.as_ref().map(|value| value.as_str()),
            Some("market:binance:spot:BTCUSDT")
        );
        assert_eq!(query.observation_kind, Some(ObservationKind::Quote));
        assert_eq!(
            query.provider.as_ref().map(|value| value.as_str()),
            Some("binance")
        );
        assert!(query.configured_only);
        assert!(query.ready_only);
    }

    #[test]
    fn connected_workspace_target_does_not_require_launch_identity() {
        let cli = Cli::try_parse_from([
            "kairos-market-cli",
            "--workspace",
            "/tmp/workspace",
            "connected",
            "snapshot",
            "quote",
            "--market-id",
            "market:binance:spot:BTCUSDT",
        ])
        .unwrap();
        let Command::Connected(ConnectedCommand::Snapshot(command)) = cli.command else {
            panic!("expected connected snapshot command");
        };

        assert!(command.target.launch_id.is_none());
        assert!(command.target.instance_id.is_none());
    }

    #[test]
    fn connected_target_rejects_partial_launch_identity() {
        let result = Cli::try_parse_from([
            "kairos-market-cli",
            "--workspace",
            "/tmp/workspace",
            "connected",
            "snapshot",
            "--launch-id",
            "launch",
            "quote",
            "--market-id",
            "market:binance:spot:BTCUSDT",
        ]);

        assert!(result.is_err());
    }

    #[test]
    fn connected_routes_rejects_unknown_observation_kind() {
        let cli = Cli::try_parse_from([
            "kairos-market-cli",
            "connected",
            "routes",
            "--launch-id",
            "launch",
            "--instance-id",
            "instance",
            "--socket",
            "/tmp/market.sock",
            "--observation-kind",
            "mystery",
        ])
        .unwrap();
        let Command::Connected(ConnectedCommand::Routes(command)) = cli.command else {
            panic!("expected connected routes command");
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
    fn unavailable_connected_route_returns_a_structured_result() {
        let value = route_not_available_json(
            "market:binance:spot:BTCUSDT",
            &Provider::new("binance").unwrap(),
            Some(ObservationKind::Quote),
            "quote",
        );

        assert_eq!(value["status"], "unavailable");
        assert_eq!(value["error"]["code"], "route_not_available");
        assert_eq!(value["error"]["retryable"], false);
        assert_eq!(
            value["error"]["details"]["next_action"],
            "list Market routes before reading a view"
        );
    }

    #[test]
    fn unready_connected_route_returns_a_retryable_structured_result() {
        let value = route_not_ready_json(
            "market:binance:spot:BTCUSDT",
            &Provider::new("binance").unwrap(),
            Some(ObservationKind::Quote),
            "quote",
        );

        assert_eq!(value["status"], "unavailable");
        assert_eq!(value["error"]["code"], "route_not_ready");
        assert_eq!(value["error"]["retryable"], true);
    }

    #[test]
    fn direct_quote_table_uses_market_labels_instead_of_internal_keys() {
        let snapshot = CliDirectObservationResult::Quote(CliMarketQuoteResult {
            symbol: "AAPL".into(),
            data_type: "quote",
            provider: "massive".into(),
            bid_price: Some(kairos_primitives::decimal::Price::new(30_955, 2).unwrap()),
            bid_quantity: Some(kairos_primitives::decimal::Quantity::new(40, 0).unwrap()),
            ask_price: Some(kairos_primitives::decimal::Price::new(30_969, 2).unwrap()),
            ask_quantity: Some(kairos_primitives::decimal::Quantity::new(40, 0).unwrap()),
            last_price: None,
            observed_at_unix_nanos: 1_700_000_000_000_000_000_u64.into(),
        });

        let output = render_direct_snapshot(&snapshot);

        assert!(output.contains("卖一 Ask"));
        assert!(output.contains("309.69 × 40"));
        assert!(output.contains("买一 Bid"));
        assert!(output.contains("价差"));
        assert!(output.contains("报价时间"));
        assert!(!output.contains("ask_price"));
        assert!(!output.contains("data_type"));
    }

    #[test]
    fn direct_bar_table_uses_ohlc_labels_and_time() {
        let price = kairos_primitives::decimal::Price::new(30_969, 2).unwrap();
        let snapshot = CliDirectObservationResult::Bar(CliMarketBarResult {
            symbol: "AAPL".into(),
            data_type: "bar",
            provider: "massive".into(),
            interval: "1m".into(),
            open: price,
            high: price,
            low: price,
            close: price,
            volume: Some(kairos_primitives::decimal::Quantity::new(581, 0).unwrap()),
            opened_at_unix_nanos: 1_700_000_000_000_000_000_u64.into(),
            closed_at_unix_nanos: None,
        });

        let output = render_direct_snapshot(&snapshot);

        assert!(output.contains("K 线开始"));
        assert!(output.contains("开盘"));
        assert!(output.contains("最高"));
        assert!(output.contains("最低"));
        assert!(output.contains("收盘"));
        assert!(output.contains("成交量"));
        assert!(output.contains("581"));
        assert!(!output.contains("data_type"));
    }
}
