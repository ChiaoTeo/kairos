use std::path::{Path, PathBuf};

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
    historical_connection: Option<crate::services::direct::DirectHistoricalConnection>,
    historical_reference: Option<kairos_reference_contract::ReferenceCatalog>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bid_venue_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ask_venue_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tape: Option<u32>,
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
    BinanceUsdMRest,
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
            historical_connection: None,
            historical_reference: None,
        }
    }

    pub(crate) fn with_direct_connection(
        workspace_root: Option<&Path>,
        direct_connection: crate::services::direct::DirectMarketConnection,
    ) -> Self {
        Self {
            workspace_root: workspace_root.map(Path::to_path_buf),
            direct_connection: Some(direct_connection),
            historical_connection: None,
            historical_reference: None,
        }
    }

    pub(crate) fn with_historical_connection(
        workspace_root: Option<&Path>,
        historical_connection: crate::services::direct::DirectHistoricalConnection,
        historical_reference: Option<kairos_reference_contract::ReferenceCatalog>,
    ) -> Self {
        Self {
            workspace_root: workspace_root.map(Path::to_path_buf),
            direct_connection: None,
            historical_connection: Some(historical_connection),
            historical_reference,
        }
    }

    pub fn validate_market(
        &mut self,
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
        &mut self,
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
        let start_time_unix_nanos = millis_to_nanos(request.start_unix_millis)?;
        let end_time_unix_nanos = millis_to_nanos(request.end_unix_millis)?;
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
        let mut observations = self
            .historical_connection
            .as_mut()
            .ok_or("historical download was not composed with a provider connection")?
            .fetch(
                match request.data_kind {
                    CliMarketHistoricalDataKind::Bar => {
                        crate::services::direct::DirectHistoricalKind::Bar
                    },
                    CliMarketHistoricalDataKind::Quote => {
                        crate::services::direct::DirectHistoricalKind::Quote
                    },
                    CliMarketHistoricalDataKind::Trade => {
                        crate::services::direct::DirectHistoricalKind::Trade
                    },
                },
                kairos_primitives::integration::ParticipantSymbol::new(request.symbol.clone())?,
                start_time_unix_nanos,
                end_time_unix_nanos,
                request.interval.clone(),
                request.adjusted,
                aggregate_scope.clone(),
                InstrumentId::new(&instrument_id)?,
                Provider::new(request.provider.as_str())?,
            )
            .await?;
        if let Some(reference) = self.historical_reference.as_ref() {
            for observation in &mut observations {
                let crate::MarketObservation::Quote(quote) = observation else {
                    continue;
                };
                if quote.bid_venue_code.is_none() && quote.ask_venue_code.is_none() {
                    continue;
                }
                // One quote decision joins both side identities at one catalog watermark.
                let session = reference.read_session()?;
                for (code, identity) in [
                    (&quote.bid_venue_code, &mut quote.bid_venue_id),
                    (&quote.ask_venue_code, &mut quote.ask_venue_id),
                ] {
                    let Some(code) = code else {
                        continue;
                    };
                    let venue = session
                        .resolve_venue_identifier(
                            &kairos_reference_contract::VenueIdentifierResolutionQuery {
                                provider: quote.provider.clone(),
                                provider_product: match request.market_type {
                                    CliMarketHistoricalMarketType::Equity => "equity",
                                    CliMarketHistoricalMarketType::Option => "options",
                                    CliMarketHistoricalMarketType::Spot => "spot",
                                }
                                .into(),
                                identifier_kind:
                                    kairos_reference_contract::VenueIdentifierKind::Exchange,
                                identifier: code.clone(),
                            },
                        )?
                        .venue;
                    *identity = crate::services::source::execution_venue_id(venue.as_ref());
                }
            }
        }
        let output = request.file.clone();
        if let Some(parent) = output
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            std::fs::create_dir_all(parent)?;
        }
        let mut body = String::new();
        let mut count = 0usize;
        for observation in observations {
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

#[cfg(test)]
mod quote_tests {
    #[test]
    fn standalone_quote_result_preserves_provider_evidence_without_claiming_canonical_ids() {
        let quote = kairos_conflux::MarketQuote {
            symbol: kairos_primitives::integration::ParticipantSymbol::new("AAPL").unwrap(),
            venue: kairos_conflux::MarketVenueEvidence {
                bid_exchange: Some("19".into()),
                ask_exchange: Some("11".into()),
                tape: Some(3),
                ..Default::default()
            },
            bid_price: None,
            bid_quantity: None,
            ask_price: None,
            ask_quantity: None,
            last_price: None,
            observed_at_unix_nanos: 7.into(),
        };
        let result = super::quote_snapshot(quote.into(), "massive");
        let json = serde_json::to_value(result).unwrap();
        assert_eq!(json["bid_venue_code"], "19");
        assert_eq!(json["ask_venue_code"], "11");
        assert_eq!(json["tape"], 3);
        assert!(json.get("bid_venue_id").is_none());
        assert!(json.get("ask_venue_id").is_none());
    }
}

fn quote_snapshot(
    value: crate::services::direct::DirectQuoteSnapshot,
    provider: &str,
) -> CliMarketQuoteResult {
    CliMarketQuoteResult {
        bid_venue_code: value.bid_venue_code,
        ask_venue_code: value.ask_venue_code,
        tape: value.tape,
        symbol: value.symbol,
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

fn trade_snapshot(
    value: crate::services::direct::DirectTradeSnapshot,
    provider: &str,
) -> CliMarketTradeResult {
    CliMarketTradeResult {
        symbol: value.symbol,
        data_type: "trade",
        provider: provider.to_owned(),
        price: value.price,
        quantity: value.quantity,
        is_buyer_maker: value.is_buyer_maker,
        event_at_unix_nanos: value.event_at_unix_nanos,
    }
}

fn bar_snapshot(
    value: crate::services::direct::DirectBarSnapshot,
    provider: &str,
) -> CliMarketBarResult {
    CliMarketBarResult {
        symbol: value.symbol,
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

fn order_book_snapshot(
    value: crate::services::direct::DirectOrderBookSnapshot,
    provider: &str,
) -> CliMarketOrderBookResult {
    CliMarketOrderBookResult {
        symbol: value.symbol,
        data_type: "order_book",
        provider: provider.to_owned(),
        bids: value.bids,
        asks: value.asks,
    }
}

fn greeks_snapshot(
    value: crate::services::direct::DirectGreeksSnapshot,
    provider: &str,
) -> CliMarketGreeksResult {
    CliMarketGreeksResult {
        symbol: value.symbol,
        data_type: "option_greeks",
        provider: provider.to_owned(),
        expiry_unix_nanos: value.expiry_unix_nanos,
        strike: value.strike,
        delta: value.delta,
        gamma: value.gamma,
        vega: value.vega,
        theta: value.theta,
        implied_volatility: value.implied_volatility,
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
