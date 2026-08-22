use std::path::{Path, PathBuf};

use kairos_conflux::{
    BinanceRestConfig, ConfluxSystem, ConnectionKey, HistoricalBarQuery, HistoricalBarRequest,
    HistoricalQuoteQuery, HistoricalTradeQuery, HistoricalWindow, MarketEvent, MarketEventKind,
    MassiveInstrumentQuery as InstrumentQuery, MassiveRestConfig, load_workspace_credential,
};
use kairos_primitives::market::SourceId;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::reference::{InstrumentKind, ReferenceStatus};
use kairos_reference_contract::{
    ReferenceProjectionSnapshot, ReferenceSqliteReader, SqliteMarketQuery,
};
use kairos_workspace::Workspace;
use serde_json::{Value, json};

use crate::application::{
    MarketApplication, ResolvedMarket, SubscriptionId, load_replay_events_many,
};
use crate::composition::{
    DiagnosticProvider, MarketCompositionConfig, attach_replay_source,
    project_reference_market_universe, run_diagnostic_once,
};

/// Standalone Market CLI facade.
///
/// This facade owns one direct/local Market CLI invocation. It may create
/// provider connections for one-shot diagnostics, but it must not connect to
/// or operate the running Market server.
pub struct CliMarketApplication {
    workspace_root: Option<PathBuf>,
}

#[derive(Clone, Copy, Debug)]
pub enum CliMarketDiagnosticProvider {
    BinanceSpotRest,
    BinanceSpotWebsocket,
    BinanceOptionsRest,
}

#[derive(Clone, Copy, Debug)]
pub enum CliMarketHistoricalProvider {
    Binance,
    Massive,
}

impl CliMarketHistoricalProvider {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Binance => "binance",
            Self::Massive => "massive",
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub enum CliMarketHistoricalMarketType {
    Equity,
    Option,
}

impl CliMarketHistoricalMarketType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Equity => "equity",
            Self::Option => "option",
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub enum CliMarketHistoricalDataKind {
    Bar,
    Quote,
    Trade,
}

impl CliMarketHistoricalDataKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Bar => "bar",
            Self::Quote => "quote",
            Self::Trade => "trade",
        }
    }
}

#[derive(Clone, Debug)]
pub struct CliMarketHistoricalDownloadRequest {
    pub provider: CliMarketHistoricalProvider,
    pub api_key: Option<String>,
    pub credential_id: Option<String>,
    pub endpoint: Option<String>,
    pub symbol: String,
    pub market_type: CliMarketHistoricalMarketType,
    pub data_kind: CliMarketHistoricalDataKind,
    pub market_id: Option<String>,
    pub instrument_id: Option<String>,
    pub network_id: Option<String>,
    pub start_unix_millis: i64,
    pub end_unix_millis: i64,
    pub interval: String,
    pub adjusted: bool,
    pub dataset_id: String,
    pub file: PathBuf,
}

impl CliMarketDiagnosticProvider {
    const fn into_diagnostic(self) -> DiagnosticProvider {
        match self {
            Self::BinanceSpotRest => DiagnosticProvider::BinanceSpotRest,
            Self::BinanceSpotWebsocket => DiagnosticProvider::BinanceSpotWebsocket,
            Self::BinanceOptionsRest => DiagnosticProvider::BinanceOptionsRest,
        }
    }
}

impl CliMarketApplication {
    pub fn open(workspace_root: Option<&Path>) -> Self {
        Self {
            workspace_root: workspace_root.map(Path::to_path_buf),
        }
    }

    pub fn validate_market(
        &self,
        market: ResolvedMarket,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let _ = self;
        market.validate()?;
        Ok(json!({
            "valid": true,
            "market": market,
        }))
    }

    pub fn reference_universe(
        &self,
        instrument_kind: InstrumentKind,
        limit: u64,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let workspace_root = self
            .workspace_root
            .as_ref()
            .ok_or("reference-universe requires --workspace")?;
        let workspace = Workspace::open(workspace_root)?;
        let database = workspace.child(&["state", "reference", "reference.sqlite"])?;
        let reader = ReferenceSqliteReader::open(&database)?;
        let projection = reader.projection(&SqliteMarketQuery {
            instrument_kind: Some(instrument_kind),
            statuses: vec![ReferenceStatus::Active, ReferenceStatus::Trading],
            limit,
            ..SqliteMarketQuery::default()
        })?;
        let snapshot = ReferenceProjectionSnapshot {
            generation: projection.watermark.generation,
            event_sequence: projection.watermark.event_sequence,
            instruments: projection.instruments.into_values().collect(),
            markets: projection.markets,
            ..ReferenceProjectionSnapshot::default()
        };
        let config = MarketCompositionConfig::load(&workspace)?;
        let update = project_reference_market_universe(&snapshot, &config.sources)?;
        let massive_option_routes = update
            .markets
            .iter()
            .filter(|market| {
                market.route.provider_id == "massive"
                    && market.route.provider_product == "options"
                    && market.instrument_kind == InstrumentKind::Option
            })
            .count();
        let sample = update
            .markets
            .iter()
            .find(|market| {
                market.route.provider_id == "massive"
                    && market.route.provider_product == "options"
                    && market.instrument_kind == InstrumentKind::Option
            })
            .map(serde_json::to_value)
            .transpose()?;
        Ok(json!({
            "generation": update.generation,
            "event_sequence": update.event_sequence,
            "reference_markets": snapshot.markets.len(),
            "projected_markets": update.markets.len(),
            "massive_option_routes": massive_option_routes,
            "sample": sample,
        }))
    }

    pub async fn once(
        &self,
        market: ResolvedMarket,
        provider: CliMarketDiagnosticProvider,
        endpoint: String,
        actor_id: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let _ = self;
        let mut runtime = MarketApplication::new(actor_id, 10_000)?;
        runtime.subscribe_static(SubscriptionId::new("cli-once")?, "cli", market)?;
        let runtime = run_diagnostic_once(runtime, provider.into_diagnostic(), endpoint).await?;
        Ok(serde_json::to_value(runtime.current_view())?)
    }

    pub async fn replay(
        &self,
        market: ResolvedMarket,
        files: Vec<PathBuf>,
        actor_id: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let _ = self;
        let events = load_replay_events_many(files)?;
        let mut runtime = MarketApplication::new(actor_id, 10_000)?;
        attach_replay_source(&mut runtime, events)?;
        runtime.subscribe_static(SubscriptionId::new("cli-replay")?, "cli", market)?;
        runtime.sync_source_subscriptions().await?;
        let mut count = 0;
        while !runtime.sources_complete() {
            count += runtime.drive_next_source_input().await?;
        }
        let mut snapshot = serde_json::to_value(runtime.current_view())?;
        if let Some(object) = snapshot.as_object_mut() {
            object.insert(
                "event_sequence".into(),
                serde_json::json!(runtime.event_sequence()),
            );
        }
        Ok(json!({"events_applied": count, "snapshot": snapshot}))
    }

    pub async fn download_historical(
        &self,
        request: CliMarketHistoricalDownloadRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let workspace = self
            .workspace_root
            .as_ref()
            .map(Workspace::open)
            .transpose()?;
        let configured_endpoint = workspace.as_ref().and_then(|workspace| {
            let reference: toml::Value = workspace.read_section("reference").ok()?;
            reference
                .get("providers")?
                .get(request.provider.as_str())?
                .get("endpoint")?
                .as_str()
                .map(str::to_owned)
        });
        let endpoint = request
            .endpoint
            .clone()
            .or(configured_endpoint)
            .unwrap_or_else(|| match request.provider {
                CliMarketHistoricalProvider::Massive => "https://api.massive.com".into(),
                CliMarketHistoricalProvider::Binance => "https://data-api.binance.vision".into(),
            });
        let start_time_unix_nanos = millis_to_nanos(request.start_unix_millis)?;
        let end_time_unix_nanos = millis_to_nanos(request.end_unix_millis)?;
        let window = HistoricalWindow {
            symbol: kairos_primitives::integration::ParticipantSymbol::new(request.symbol.clone())
                .map_err(|error| error.to_string())?,
            start_time_unix_nanos,
            end_time_unix_nanos,
        };
        let bar_request = HistoricalBarRequest {
            window: window.clone(),
            interval: request.interval.clone(),
            adjusted: Some(request.adjusted),
        };
        let events = match request.provider {
            CliMarketHistoricalProvider::Massive => {
                let api_key = if let Some(value) = request.api_key.clone() {
                    value
                } else {
                    let _workspace_root = self.workspace_root.as_ref().ok_or(
                        "Massive download requires --workspace or the deprecated --api-key",
                    )?;
                    let workspace = workspace
                        .as_ref()
                        .ok_or("Massive download workspace could not be opened")?;
                    let credentials_root =
                        workspace.existing_path(&["config", "credentials"], &["credentials"])?;
                    load_workspace_credential(
                        &credentials_root,
                        "massive",
                        request.credential_id.as_deref(),
                    )?
                    .ok_or("Massive workspace credential does not exist")?
                    .api_key
                };
                let key = ConnectionKey::new("market-history")?;
                let mut system = ConfluxSystem::new();
                system.connections().massive_rest.create(
                    key.clone(),
                    MassiveRestConfig {
                        environment: "public".into(),
                        endpoint,
                        api_key: secrecy::SecretString::new(api_key.into()),
                        instrument_query: match request.market_type {
                            CliMarketHistoricalMarketType::Equity => InstrumentQuery::equities(),
                            CliMarketHistoricalMarketType::Option => InstrumentQuery::options(None),
                        },
                    },
                )?;
                let mut connections = system.connections();
                let provider = connections.massive_rest.get(&key)?;
                fetch_historical(provider, request.data_kind, &window, &bar_request).await?
            },
            CliMarketHistoricalProvider::Binance => {
                let key = ConnectionKey::new("market-history")?;
                let mut system = ConfluxSystem::new();
                system.connections().binance_spot_rest.create(
                    key.clone(),
                    BinanceRestConfig {
                        environment: "public".into(),
                        endpoint,
                        credential: None,
                    },
                )?;
                let mut connections = system.connections();
                let provider = connections.binance_spot_rest.get(&key)?;
                fetch_historical(provider, request.data_kind, &window, &bar_request).await?
            },
        };
        let instrument_id = request
            .instrument_id
            .clone()
            .ok_or("historical download requires Reference-owned --instrument-id")?;
        let aggregate_scope = match request.provider {
            CliMarketHistoricalProvider::Massive => {
                crate::ObservationScope::consolidated(&instrument_id, request.network_id.clone())?
            },
            CliMarketHistoricalProvider::Binance => crate::ObservationScope::market(
                request
                    .market_id
                    .as_deref()
                    .ok_or("Binance historical download requires canonical --market-id")?,
            )?,
        };
        let output = request.file.clone();
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
                    crate::MarketObservation::Bar(crate::Bar {
                        scope: aggregate_scope.clone(),
                        instrument_id: InstrumentId::new(&instrument_id)?,
                        timeframe: bar.timeframe,
                        open: bar.open,
                        high: bar.high,
                        low: bar.low,
                        close: bar.close,
                        volume: bar.volume,
                        observed_at_unix_nanos: event.observed_at_unix_nanos,
                        source_id: SourceId::new(request.provider.as_str())?,
                        derivation: bar.derivation,
                    })
                },
                MarketEventKind::Quote => crate::MarketObservation::Quote(crate::Quote {
                    scope: aggregate_scope.clone(),
                    instrument_id: InstrumentId::new(&instrument_id)?,
                    bid_price: event.price,
                    bid_quantity: event.quantity,
                    ask_price: event.ask_price,
                    ask_quantity: event.ask_quantity,
                    bid_venue_code: event.venue.bid_exchange,
                    ask_venue_code: event.venue.ask_exchange,
                    tape: event.venue.tape,
                    observed_at_unix_nanos: event.observed_at_unix_nanos,
                    source_id: SourceId::new(request.provider.as_str())?,
                }),
                MarketEventKind::Trade => {
                    if matches!(request.provider, CliMarketHistoricalProvider::Massive) {
                        return Err(
                            "Massive historical trades require an explicit venue-code to canonical-market join; observation quarantined"
                                .into(),
                        );
                    }
                    crate::MarketObservation::Trade(crate::Trade {
                        scope: aggregate_scope.clone(),
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
                        venue_code: event.venue.trade_exchange,
                        tape: event.venue.tape,
                        trf_id: event.venue.trf_id,
                        participant_timestamp_unix_nanos: event
                            .venue
                            .participant_timestamp_unix_nanos,
                        trf_timestamp_unix_nanos: event.venue.trf_timestamp_unix_nanos,
                        observed_at_unix_nanos: event.observed_at_unix_nanos,
                        source_id: SourceId::new(request.provider.as_str())?,
                    })
                },
                _ => continue,
            };
            body.push_str(&serde_json::to_string(&observation)?);
            body.push('\n');
            count += 1;
        }
        std::fs::write(&output, body)?;
        let manifest = serde_json::json!({
            "dataset_id": request.dataset_id,
            "provider": request.provider.as_str(),
            "source": request.provider.as_str(),
            "symbol": request.symbol,
            "scope_key": aggregate_scope.key(),
            "market_id": aggregate_scope.market_id().map(ToString::to_string),
            "instrument_id": instrument_id,
            "data_kind": request.data_kind.as_str(),
            "observation_type": request.data_kind.as_str(),
            "market_type": request.market_type.as_str(),
            "interval": request.interval,
            "timeframe": request.interval,
            "adjusted": request.adjusted,
            "start_time_unix_millis": request.start_unix_millis,
            "end_time_unix_millis": request.end_unix_millis,
            "event_count": count,
            "path": output,
            "format": "jsonl",
        });
        let manifest_path = output.with_extension("manifest.json");
        std::fs::write(&manifest_path, serde_json::to_vec_pretty(&manifest)?)?;
        if let Some(workspace_root) = self.workspace_root.as_ref() {
            register_dataset(workspace_root, &manifest, &output, &manifest_path)?;
        }
        Ok(manifest)
    }
}

async fn fetch_historical<C>(
    connection: &mut C,
    kind: CliMarketHistoricalDataKind,
    window: &HistoricalWindow,
    bar_request: &HistoricalBarRequest,
) -> Result<Vec<MarketEvent>, kairos_conflux::IntegrationError>
where
    C: HistoricalBarQuery + HistoricalQuoteQuery + HistoricalTradeQuery,
{
    let venue = kairos_conflux::MarketVenueEvidence::default();
    match kind {
        CliMarketHistoricalDataKind::Bar => Ok(connection
            .fetch_bars(bar_request)
            .await?
            .into_iter()
            .map(|bar| MarketEvent {
                symbol: bar.symbol,
                kind: MarketEventKind::Bar,
                price: None,
                quantity: None,
                rate: None,
                ask_price: None,
                ask_quantity: None,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: Some(kairos_conflux::Bar {
                    timeframe: bar.interval,
                    open: bar.open,
                    high: bar.high,
                    low: bar.low,
                    close: bar.close,
                    volume: bar.volume,
                    derivation: bar.derivation,
                }),
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: None,
                observed_at_unix_nanos: bar.opened_at_unix_nanos,
                venue: venue.clone(),
            })
            .collect()),
        CliMarketHistoricalDataKind::Quote => Ok(connection
            .fetch_quotes(window)
            .await?
            .into_iter()
            .map(|quote| MarketEvent {
                symbol: quote.symbol,
                kind: MarketEventKind::Quote,
                price: quote.bid_price.or(quote.last_price),
                quantity: quote.bid_quantity,
                rate: None,
                ask_price: quote.ask_price,
                ask_quantity: quote.ask_quantity,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: None,
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: None,
                observed_at_unix_nanos: quote.observed_at_unix_nanos,
                venue: venue.clone(),
            })
            .collect()),
        CliMarketHistoricalDataKind::Trade => Ok(connection
            .fetch_trades(window)
            .await?
            .into_iter()
            .map(|trade| MarketEvent {
                symbol: trade.symbol,
                kind: MarketEventKind::Trade,
                price: Some(trade.price),
                quantity: Some(trade.quantity),
                rate: None,
                ask_price: None,
                ask_quantity: None,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: None,
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: trade
                    .participant_trade_id
                    .as_deref()
                    .and_then(|value| value.parse::<u64>().ok())
                    .map(Into::into),
                observed_at_unix_nanos: trade.event_at_unix_nanos,
                venue: venue.clone(),
            })
            .collect()),
    }
}

fn millis_to_nanos(value: i64) -> Result<kairos_primitives::time::UnixNanos, String> {
    let value = u64::try_from(value).map_err(|_| "historical time must be non-negative")?;
    value
        .checked_mul(1_000_000)
        .map(kairos_primitives::time::UnixNanos::new)
        .ok_or_else(|| "historical time is out of range".into())
}

fn register_dataset(
    workspace_root: &Path,
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
