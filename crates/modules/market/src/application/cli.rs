use std::path::{Path, PathBuf};

use kairos_conflux::{
    BinanceRestConfig, BinanceSpotRestConnection, ConnectionKey, HistoricalBarQuery,
    HistoricalBarRequest, HistoricalQuoteQuery, HistoricalTradeQuery, HistoricalWindow, MarketBar,
    MarketEvent, MarketEventKind, MarketGreeks, MarketOrderBook, MarketQuote, MarketTrade,
    MassiveInstrumentQuery as InstrumentQuery, MassiveRestConfig, MassiveRestConnection,
    load_workspace_credential,
};
use kairos_primitives::decimal::{Price, Quantity, Rate};
use kairos_primitives::market::{ObservationKind, Provider};
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
use kairos_workspace::Workspace;
use serde::{Deserialize, Serialize};

use crate::application::{
    MarketApplication, ResolvedMarket, SubscriptionId, load_replay_events_many,
};
use crate::domain::source::MarketFeedId;

/// Standalone, bounded Market CLI facade.
///
/// Snapshot queries own only a short-lived provider REST connection. They do
/// not construct the stateful Market application, Actor, subscriptions, or a
/// Conflux process. Replay remains stateful because it projects an event log.
pub struct CliMarketApplication {
    workspace_root: Option<PathBuf>,
    direct_connection: Option<crate::services::direct::DirectMarketConnection>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketReplayResult {
    pub events_applied: usize,
    pub snapshot: CliMarketReplaySnapshot,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketReplaySnapshot {
    pub event_sequence: kairos_primitives::time::Sequence,
    #[serde(flatten)]
    view: crate::domain::view::MarketView,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketRoutesResult {
    pub routes: Vec<CliMarketRouteResult>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketRouteResult {
    pub provider: Provider,
    pub observation_kinds: Vec<&'static str>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketValidationResult {
    pub valid: bool,
    pub market: ResolvedMarket,
}

#[derive(Clone, Debug, Serialize)]
#[serde(untagged)]
pub enum CliDirectObservationResult {
    Quote(CliMarketQuoteResult),
    Trade(CliMarketTradeResult),
    Bar(CliMarketBarResult),
    OrderBook(CliMarketOrderBookResult),
    Greeks(CliMarketGreeksResult),
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketQuoteResult {
    pub symbol: String,
    pub data_type: &'static str,
    pub provider: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bid_price: Option<Price>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bid_quantity: Option<Quantity>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ask_price: Option<Price>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ask_quantity: Option<Quantity>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketTradeResult {
    pub symbol: String,
    pub data_type: &'static str,
    pub provider: String,
    pub price: Price,
    pub quantity: Quantity,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub is_buyer_maker: Option<bool>,
    pub event_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketBarResult {
    pub symbol: String,
    pub data_type: &'static str,
    pub provider: String,
    pub interval: String,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub volume: Option<Quantity>,
    pub opened_at_unix_nanos: UnixNanos,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub closed_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketOrderBookResult {
    pub symbol: String,
    pub data_type: &'static str,
    pub provider: String,
    pub bids: Vec<(Price, Quantity)>,
    pub asks: Vec<(Price, Quantity)>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CliMarketGreeksResult {
    pub symbol: String,
    pub data_type: &'static str,
    pub provider: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expiry_unix_nanos: Option<UnixNanos>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub strike: Option<Price>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub delta: Option<Rate>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gamma: Option<Rate>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vega: Option<Rate>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub theta: Option<Rate>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub implied_volatility: Option<Rate>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CliMarketDatasetManifest {
    pub dataset_id: String,
    pub version: u32,
    pub providers: std::collections::BTreeSet<Provider>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub derivation: Option<String>,
    pub symbol: String,
    pub scope_key: String,
    pub market_id: Option<String>,
    pub instrument_id: String,
    pub data_kind: String,
    pub observation_type: String,
    pub market_type: String,
    pub interval: String,
    pub timeframe: String,
    pub adjusted: bool,
    pub start_time_unix_millis: i64,
    pub end_time_unix_millis: i64,
    pub event_count: usize,
    pub path: PathBuf,
    pub format: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CliMarketDatasetCatalogEntry {
    #[serde(flatten)]
    pub manifest: CliMarketDatasetManifest,
    pub name: String,
    pub manifest_path: PathBuf,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct CliMarketDatasetsResult {
    pub datasets: Vec<CliMarketDatasetCatalogEntry>,
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub aliases: std::collections::BTreeMap<String, String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CliMarketOnceProvider {
    BinanceSpotRest,
    BinanceEquityRest,
    BinanceOptionsRest,
    MassiveRest,
}

#[derive(Clone, Debug)]
pub struct CliMarketRoute {
    pub provider: Provider,
    pub connection: CliMarketOnceProvider,
    pub observation_kinds: Vec<ObservationKind>,
}

#[derive(Clone, Debug)]
pub struct CliMarketOnceRequest {
    pub provider: Provider,
    pub connection: CliMarketOnceProvider,
    pub symbol: String,
    pub observation_kind: ObservationKind,
    pub endpoint: Option<String>,
    pub interval: String,
    pub depth: u32,
    pub credential_id: Option<String>,
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
    Spot,
    Equity,
    Option,
}

impl CliMarketHistoricalMarketType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Spot => "spot",
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

impl CliMarketApplication {
    pub fn direct_routes(&self, routes: Vec<CliMarketRoute>) -> CliMarketRoutesResult {
        let _ = self;
        CliMarketRoutesResult {
            routes: routes
                .into_iter()
                .map(|route| CliMarketRouteResult {
                    provider: route.provider,
                    observation_kinds: route
                        .observation_kinds
                        .into_iter()
                        .map(|kind| kind.as_str())
                        .collect::<Vec<_>>(),
                })
                .collect::<Vec<_>>(),
        }
    }

    pub fn open(workspace_root: Option<&Path>) -> Self {
        Self {
            workspace_root: workspace_root.map(Path::to_path_buf),
            direct_connection: None,
        }
    }

    pub(crate) fn with_direct_connection(
        workspace_root: Option<&Path>,
        direct_connection: crate::services::direct::DirectMarketConnection,
    ) -> Self {
        Self {
            workspace_root: workspace_root.map(Path::to_path_buf),
            direct_connection: Some(direct_connection),
        }
    }

    pub fn validate_market(
        &self,
        market: ResolvedMarket,
    ) -> Result<CliMarketValidationResult, Box<dyn std::error::Error>> {
        let _ = self;
        market.validate()?;
        Ok(CliMarketValidationResult {
            valid: true,
            market,
        })
    }

    pub async fn once(
        &mut self,
        request: CliMarketOnceRequest,
    ) -> Result<CliDirectObservationResult, Box<dyn std::error::Error>> {
        let symbol = kairos_primitives::integration::ParticipantSymbol::new(&request.symbol)
            .map_err(|error| error.to_string())?;
        let value = self
            .direct_connection
            .as_mut()
            .ok_or("standalone Market query was not composed with a provider connection")?
            .snapshot(
                &symbol,
                request.observation_kind,
                &request.interval,
                request.depth,
            )
            .await?;
        let provider = request.provider.as_str();
        Ok(match value {
            crate::services::direct::DirectMarketSnapshot::Quote(value) => {
                CliDirectObservationResult::Quote(quote_snapshot(value, provider))
            },
            crate::services::direct::DirectMarketSnapshot::Trade(value) => {
                CliDirectObservationResult::Trade(trade_snapshot(value, provider))
            },
            crate::services::direct::DirectMarketSnapshot::Bar(value) => {
                CliDirectObservationResult::Bar(bar_snapshot(value, provider))
            },
            crate::services::direct::DirectMarketSnapshot::OrderBook(value) => {
                CliDirectObservationResult::OrderBook(order_book_snapshot(value, provider))
            },
            crate::services::direct::DirectMarketSnapshot::Greeks(value) => {
                CliDirectObservationResult::Greeks(greeks_snapshot(value, provider))
            },
        })
    }

    pub async fn replay(
        &self,
        market: ResolvedMarket,
        files: Vec<PathBuf>,
        actor_id: String,
    ) -> Result<CliMarketReplayResult, Box<dyn std::error::Error>> {
        let _ = self;
        let events = load_replay_events_many(files)?;
        let mut runtime = MarketApplication::new(actor_id, 10_000)?;
        let source = crate::services::source::ReplaySource::new(events);
        let descriptor =
            crate::domain::source::FeedDescriptor::all_routes(MarketFeedId::new("replay")?);
        let handle = crate::services::source::spawn_replay(
            descriptor,
            source,
            runtime.source_input_capacity(),
        );
        runtime.attach_source(handle)?;
        runtime.subscribe_static(SubscriptionId::new("cli-replay")?, "cli", market)?;
        runtime.sync_source_subscriptions().await?;
        let mut count = 0;
        while !runtime.sources_complete() {
            count += runtime.drive_next_source_input().await?;
        }
        Ok(CliMarketReplayResult {
            events_applied: count,
            snapshot: CliMarketReplaySnapshot {
                event_sequence: runtime.event_sequence().into(),
                view: runtime.current_view(),
            },
        })
    }

    pub async fn download_historical(
        &self,
        request: CliMarketHistoricalDownloadRequest,
    ) -> Result<CliMarketDatasetManifest, Box<dyn std::error::Error>> {
        match (request.provider, request.market_type) {
            (CliMarketHistoricalProvider::Binance, CliMarketHistoricalMarketType::Spot)
            | (
                CliMarketHistoricalProvider::Massive,
                CliMarketHistoricalMarketType::Equity | CliMarketHistoricalMarketType::Option,
            ) => {},
            _ => {
                return Err(format!(
                    "historical provider {} does not support market type {}",
                    request.provider.as_str(),
                    request.market_type.as_str()
                )
                .into());
            },
        }
        let endpoint = request
            .endpoint
            .clone()
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
                    let workspace_root = self.workspace_root.as_ref().ok_or(
                        "Massive download requires --workspace or the deprecated --api-key",
                    )?;
                    let workspace = Workspace::open(workspace_root)?;
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
                let mut provider = MassiveRestConnection::new(
                    key,
                    MassiveRestConfig {
                        environment: "public".into(),
                        endpoint,
                        api_key: secrecy::SecretString::new(api_key.into()),
                        instrument_query: match request.market_type {
                            CliMarketHistoricalMarketType::Equity => InstrumentQuery::equities(),
                            CliMarketHistoricalMarketType::Option => InstrumentQuery::options(None),
                            CliMarketHistoricalMarketType::Spot => {
                                unreachable!("provider/market type compatibility was validated")
                            },
                        },
                    },
                )?;
                fetch_historical(&mut provider, request.data_kind, &window, &bar_request).await?
            },
            CliMarketHistoricalProvider::Binance => {
                let key = ConnectionKey::new("market-history")?;
                let mut provider = BinanceSpotRestConnection::new(
                    key,
                    BinanceRestConfig {
                        environment: "public".into(),
                        endpoint,
                        credential: None,
                    },
                )?;
                fetch_historical(&mut provider, request.data_kind, &window, &bar_request).await?
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
                        provider: Provider::new(request.provider.as_str())?,
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
                    provider: Provider::new(request.provider.as_str())?,
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
                        provider: Provider::new(request.provider.as_str())?,
                    })
                },
                _ => continue,
            };
            body.push_str(&serde_json::to_string(&observation)?);
            body.push('\n');
            count += 1;
        }
        std::fs::write(&output, body)?;
        let manifest = CliMarketDatasetManifest {
            dataset_id: request.dataset_id,
            version: 1,
            providers: std::collections::BTreeSet::from([Provider::new(
                request.provider.as_str(),
            )?]),
            derivation: None,
            symbol: request.symbol,
            scope_key: aggregate_scope.key().to_owned(),
            market_id: aggregate_scope.market_id().map(ToString::to_string),
            instrument_id,
            data_kind: request.data_kind.as_str().to_owned(),
            observation_type: request.data_kind.as_str().to_owned(),
            market_type: request.market_type.as_str().to_owned(),
            interval: request.interval.clone(),
            timeframe: request.interval,
            adjusted: request.adjusted,
            start_time_unix_millis: request.start_unix_millis,
            end_time_unix_millis: request.end_unix_millis,
            event_count: count,
            path: output.clone(),
            format: "jsonl".into(),
        };
        let manifest_path = output.with_extension("manifest.json");
        std::fs::write(&manifest_path, serde_json::to_vec_pretty(&manifest)?)?;
        if let Some(workspace_root) = self.workspace_root.as_ref() {
            register_dataset(workspace_root, &manifest, &output, &manifest_path)?;
        }
        Ok(manifest)
    }

    pub fn historical_datasets(
        &self,
    ) -> Result<CliMarketDatasetsResult, Box<dyn std::error::Error>> {
        let workspace_root = self
            .workspace_root
            .as_ref()
            .ok_or("listing historical datasets requires --workspace")?;
        let workspace = Workspace::open(workspace_root)?;
        let catalog_path = workspace.state_root().join("market").join("datasets.json");
        if !catalog_path.is_file() {
            return Ok(CliMarketDatasetsResult::default());
        }
        Ok(serde_json::from_slice(&std::fs::read(catalog_path)?)?)
    }
}

fn quote_snapshot(value: MarketQuote, provider: &str) -> CliMarketQuoteResult {
    CliMarketQuoteResult {
        symbol: value.symbol.to_string(),
        data_type: "quote",
        provider: provider.to_owned(),
        bid_price: value.bid_price,
        bid_quantity: value.bid_quantity,
        ask_price: value.ask_price,
        ask_quantity: value.ask_quantity,
        last_price: value.last_price,
        observed_at_unix_nanos: value.observed_at_unix_nanos,
    }
}

fn trade_snapshot(value: MarketTrade, provider: &str) -> CliMarketTradeResult {
    CliMarketTradeResult {
        symbol: value.symbol.to_string(),
        data_type: "trade",
        provider: provider.to_owned(),
        price: value.price,
        quantity: value.quantity,
        is_buyer_maker: value.is_buyer_maker,
        event_at_unix_nanos: value.event_at_unix_nanos,
    }
}

fn bar_snapshot(value: MarketBar, provider: &str) -> CliMarketBarResult {
    CliMarketBarResult {
        symbol: value.symbol.to_string(),
        data_type: "bar",
        provider: provider.to_owned(),
        interval: value.interval,
        open: value.open,
        high: value.high,
        low: value.low,
        close: value.close,
        volume: value.volume,
        opened_at_unix_nanos: value.opened_at_unix_nanos,
        closed_at_unix_nanos: value.closed_at_unix_nanos,
    }
}

fn order_book_snapshot(value: MarketOrderBook, provider: &str) -> CliMarketOrderBookResult {
    CliMarketOrderBookResult {
        symbol: value.symbol.to_string(),
        data_type: "order_book",
        provider: provider.to_owned(),
        bids: value.bids,
        asks: value.asks,
    }
}

fn greeks_snapshot(value: MarketGreeks, provider: &str) -> CliMarketGreeksResult {
    CliMarketGreeksResult {
        symbol: value.symbol.to_string(),
        data_type: "option_greeks",
        provider: provider.to_owned(),
        expiry_unix_nanos: value.values.expiry_unix_nanos,
        strike: value.values.strike,
        delta: value.values.delta,
        gamma: value.values.gamma,
        vega: value.values.vega,
        theta: value.values.theta,
        implied_volatility: value.values.implied_volatility,
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
    manifest: &CliMarketDatasetManifest,
    output: &Path,
    manifest_path: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let dataset_id = manifest.dataset_id.as_str();
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
        serde_json::from_slice::<CliMarketDatasetsResult>(&std::fs::read(&catalog_path)?)?
    } else {
        CliMarketDatasetsResult::default()
    };
    catalog.datasets.retain(|item| item.name != dataset_id);
    let output = output.canonicalize()?;
    let manifest_path = manifest_path.canonicalize()?;
    let mut stored_manifest = manifest.clone();
    stored_manifest.path = output;
    catalog.datasets.push(CliMarketDatasetCatalogEntry {
        manifest: stored_manifest,
        name: dataset_id.to_owned(),
        manifest_path,
    });
    catalog
        .datasets
        .sort_by(|left, right| left.name.cmp(&right.name));
    let temporary = catalog_path.with_extension("json.tmp");
    std::fs::write(&temporary, serde_json::to_vec_pretty(&catalog)?)?;
    std::fs::rename(temporary, catalog_path)?;
    Ok(())
}
