//! Process composition for concrete provider feeds.

use kairos_integration::participants::binance;
use kairos_integration::participants::hyperliquid::{
    HyperliquidConnection, HyperliquidConnectionConfig,
};
use kairos_integration::participants::massive::{
    MarketType as MassiveMarketType, MassiveConnection, MassiveConnectionConfig,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig,
};
use kairos_workspace::{
    Workspace, WorkspaceBinanceDerivativeProduct, WorkspaceBinanceSpotTransport,
    WorkspaceMarketSourceBinding, WorkspaceMarketSourceBinding as Binding,
};

use crate::application::{MarketSnapshotPublisher, ReferenceChangeSource, ReferenceEvent};
use crate::domain::source::{SourceDescriptor, SourceId};
use crate::services::sources::{
    spawn_binance, spawn_replay, spawn_snapshot, spawn_stream, ReplaySource, SourceActivator,
    SourceHandle,
};
use crate::MarketApplication;

mod config;
mod diagnostic;
mod process;
mod sources;

pub use config::{
    MarketProcessRequest, MarketReplayClock, MarketReplayConfig, MarketRuntimeProfile,
    MarketRuntimeScope,
};
pub use diagnostic::{
    attach_binance_derivatives_source, attach_binance_spot_rest_source, attach_binance_spot_source,
};
pub use process::{build_market_process, MarketStartupError};

pub use kairos_market_contract::transport::AeronReferenceChangeSource;

/// Demand-driven source construction for live and paper Market processes.
///
/// The activator contains only immutable workspace/configuration facts. The
/// active source map and all subscription state remain owned by MarketActor.
pub(crate) struct WorkspaceMarketSourceActivator {
    workspace: Workspace,
}

impl WorkspaceMarketSourceActivator {
    pub(crate) fn new(workspace: Workspace) -> Self {
        Self { workspace }
    }
}

impl SourceActivator for WorkspaceMarketSourceActivator {
    fn activate<'a>(
        &'a mut self,
        market: &'a crate::MarketDescriptor,
        source_input_capacity: usize,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<SourceHandle, String>> + Send + 'a>,
    > {
        let workspace = self.workspace.clone();
        let market = market.clone();
        Box::pin(async move { activate_workspace_source(workspace, market, source_input_capacity) })
    }
}

fn activate_workspace_source(
    workspace: Workspace,
    market: crate::MarketDescriptor,
    source_input_capacity: usize,
) -> Result<SourceHandle, String> {
    let credentials_root = workspace
        .child(&["credentials"])
        .map_err(|error| error.to_string())?;
    let configured_route_exists = workspace
        .market_config()
        .sources
        .values()
        .any(|binding| binding_matches_market(binding, &market));
    let mut candidates = workspace
        .market_config()
        .sources
        .iter()
        .filter(|(id, binding)| {
            binding.enabled()
                && market
                    .source_id
                    .as_deref()
                    .is_none_or(|requested| requested.eq_ignore_ascii_case(id))
                && binding_matches_market(binding, &market)
        })
        .map(|(id, binding)| (id.clone(), binding.clone()))
        .collect::<Vec<_>>();

    // Public Binance Spot is the built-in default route. It keeps a
    // minimal workspace usable without turning provider source creation
    // into a required static Market configuration.
    if candidates.is_empty()
        && !configured_route_exists
        && market.source_id.is_none()
        && market_exchange(&market).eq_ignore_ascii_case("binance")
        && market.market_type.eq_ignore_ascii_case("spot")
    {
        candidates.push((
            "binance-spot".into(),
            Binding::BinanceSpot {
                enabled: true,
                transport: WorkspaceBinanceSpotTransport::Websocket,
                endpoint: None,
                snapshot_interval_ms: 1_000,
            },
        ));
    }
    let [(source_id, binding)] = candidates.as_slice() else {
        return Err(if candidates.is_empty() {
            format!(
                "no Market source supports exchange={} market_type={} asset_type={:?}",
                market.exchange_id, market.market_type, market.asset_type
            )
        } else {
            format!(
                "market route is ambiguous; candidates={}",
                candidates
                    .iter()
                    .map(|(id, _)| id.as_str())
                    .collect::<Vec<_>>()
                    .join(",")
            )
        });
    };

    let mut staging = crate::MarketApplication::new_with_source_capacity(
        "market-source-activation",
        1,
        source_input_capacity,
    )
    .map_err(|error| error.to_string())?;
    attach_configured_market_source(&mut staging, &credentials_root, source_id, binding)?;
    staging.take_source_handle(&SourceId::new(source_id.clone())?)
}

fn binding_matches_market(
    binding: &WorkspaceMarketSourceBinding,
    market: &crate::MarketDescriptor,
) -> bool {
    let exchange = market_exchange(market);
    let market_type = market.market_type.as_str();
    let asset_type = market.asset_type.as_deref();
    match binding {
        WorkspaceMarketSourceBinding::BinanceSpot { .. } => {
            exchange.eq_ignore_ascii_case("binance")
                && market_type.eq_ignore_ascii_case("spot")
                && asset_type.is_none_or(|value| value.eq_ignore_ascii_case("crypto"))
        }
        WorkspaceMarketSourceBinding::BinanceEquity { .. } => {
            exchange.eq_ignore_ascii_case("binance")
                && market_type.eq_ignore_ascii_case("equity")
                && asset_type.is_none_or(|value| value.eq_ignore_ascii_case("equity"))
        }
        WorkspaceMarketSourceBinding::BinanceDerivatives { product, .. } => {
            let expected = match product {
                WorkspaceBinanceDerivativeProduct::UsdMFutures => "usd-m-futures",
                WorkspaceBinanceDerivativeProduct::CoinMFutures => "coin-m-futures",
                WorkspaceBinanceDerivativeProduct::Options => "options",
            };
            exchange.eq_ignore_ascii_case("binance")
                && market_type.eq_ignore_ascii_case(expected)
                && asset_type.is_none_or(|value| value.eq_ignore_ascii_case("crypto"))
        }
        WorkspaceMarketSourceBinding::Massive { product, .. } => {
            exchange.eq_ignore_ascii_case("massive")
                && market_type.eq_ignore_ascii_case(match product {
                    kairos_workspace::WorkspaceMassiveMarketProduct::Equity => "equity",
                    kairos_workspace::WorkspaceMassiveMarketProduct::Options => "options",
                })
                && asset_type.is_none_or(|value| value.eq_ignore_ascii_case("equity"))
        }
        WorkspaceMarketSourceBinding::Okx {
            instrument_type, ..
        } => {
            exchange.eq_ignore_ascii_case("okx")
                && market_type.eq_ignore_ascii_case(match instrument_type {
                    kairos_workspace::WorkspaceOkxInstrumentType::Spot => "spot",
                    kairos_workspace::WorkspaceOkxInstrumentType::Swap => "swap",
                    kairos_workspace::WorkspaceOkxInstrumentType::Futures => "futures",
                    kairos_workspace::WorkspaceOkxInstrumentType::Options => "options",
                })
        }
        WorkspaceMarketSourceBinding::Hyperliquid {
            market_type: configured,
            ..
        } => {
            exchange.eq_ignore_ascii_case("hyperliquid")
                && market_type.eq_ignore_ascii_case(match configured {
                    kairos_workspace::WorkspaceHyperliquidMarketType::Spot => "spot",
                    kairos_workspace::WorkspaceHyperliquidMarketType::Perpetual => "perpetual",
                })
        }
    }
}

fn market_exchange(market: &crate::MarketDescriptor) -> &str {
    market
        .exchange_id
        .as_str()
        .strip_prefix("exchange:")
        .unwrap_or(market.exchange_id.as_str())
}

/// Market-owned source routing classification. Provider adapters map this to
/// their own native vocabulary at composition time.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MarketProduct {
    Spot,
    UsdMFutures,
    CoinMFutures,
    Options,
    Equity,
}

impl ReferenceChangeSource for AeronReferenceChangeSource {
    fn next_event(&mut self) -> Result<Option<ReferenceEvent>, String> {
        self.next_change()
            .map(|value| {
                value.map(|change| ReferenceEvent {
                    sequence: change.sequence.into(),
                })
            })
            .map_err(|error| error.to_string())
    }
}
pub struct MmapMarketSnapshotPublisher {
    inner: kairos_market_contract::encoding::MmapMarketSnapshotPublisher,
}

impl MmapMarketSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_market_contract::encoding::MmapMarketSnapshotPublisher::create(
                path,
                slot_size,
                actor_id,
                event_stream_id,
            )
            .map_err(|error| error.to_string())?,
        })
    }

    pub fn create_with_identity(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Result<Self, String> {
        Ok(Self {
            inner:
                kairos_market_contract::encoding::MmapMarketSnapshotPublisher::create_with_identity(
                    path,
                    slot_size,
                    actor_id,
                    event_stream_id,
                    identity,
                )
                .map_err(|error| error.to_string())?,
        })
    }

    pub fn publish(
        &mut self,
        snapshot: &crate::domain::snapshot::MarketSnapshot,
    ) -> Result<(), String> {
        let mut value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
        normalize_orderbook_decimals(&mut value)?;
        normalize_observation_decimals(&mut value)?;
        let contract: kairos_market_contract::MarketSnapshot =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        self.inner.publish(&contract)
    }
}

/// Convert domain fixed-decimal value objects to the decimal strings used by
/// the cross-process Market contract. This belongs at the composition
/// boundary; the domain keeps its typed Price and Quantity invariants while
/// the wire contract remains stable and provider-neutral.
fn normalize_orderbook_decimals(value: &mut serde_json::Value) -> Result<(), String> {
    let order_books = value
        .get_mut("order_books")
        .and_then(serde_json::Value::as_object_mut)
        .ok_or_else(|| "market snapshot is missing order_books".to_string())?;

    for book in order_books.values_mut() {
        let book = book
            .as_object_mut()
            .ok_or_else(|| "market order book must be an object".to_string())?;
        for side in ["bids", "asks"] {
            let levels = book
                .get_mut(side)
                .and_then(serde_json::Value::as_array_mut)
                .ok_or_else(|| format!("market order book is missing {side}"))?;
            for level in levels {
                let level = level
                    .as_object_mut()
                    .ok_or_else(|| "market price level must be an object".to_string())?;
                for field in ["price", "quantity"] {
                    let decimal = level
                        .get(field)
                        .ok_or_else(|| format!("market price level is missing {field}"))?;
                    level.insert(field.to_string(), fixed_decimal_string(decimal)?);
                }
            }
        }
    }
    Ok(())
}

fn normalize_observation_decimals(value: &mut serde_json::Value) -> Result<(), String> {
    for field in ["latest", "views"] {
        let Some(values) = value
            .get_mut(field)
            .and_then(serde_json::Value::as_object_mut)
        else {
            continue;
        };
        for observation in values.values_mut() {
            normalize_observation_value(observation)?;
        }
    }
    Ok(())
}

fn normalize_observation_value(value: &mut serde_json::Value) -> Result<(), String> {
    let object = value
        .as_object_mut()
        .ok_or_else(|| "market observation must be an object".to_string())?;
    for kind in ["Quote", "Trade", "Bar"] {
        if let Some(payload) = object
            .get_mut(kind)
            .and_then(serde_json::Value::as_object_mut)
        {
            let fields = match kind {
                "Quote" => ["bid_price", "bid_quantity", "ask_price", "ask_quantity"].as_slice(),
                "Trade" => ["price", "quantity", "cost"].as_slice(),
                _ => ["open", "high", "low", "close", "volume"].as_slice(),
            };
            for field in fields {
                if let Some(decimal) = payload.get_mut(*field) {
                    if !decimal.is_null() {
                        *decimal = fixed_decimal_string(decimal)?;
                    }
                }
            }
        }
    }
    for kind in ["TradeBar", "QuoteBar"] {
        if let Some(payload) = object
            .get_mut(kind)
            .and_then(serde_json::Value::as_object_mut)
        {
            if let Some(bar) = payload.get_mut("bar") {
                if let Some(bar) = bar.as_object_mut() {
                    for field in ["open", "high", "low", "close", "volume"] {
                        if let Some(decimal) = bar.get_mut(field) {
                            if !decimal.is_null() {
                                *decimal = fixed_decimal_string(decimal)?;
                            }
                        }
                    }
                }
            }
        }
    }
    let decimal_fields: &[(&str, &[&str])] = &[
        (
            "OptionGreeks",
            &[
                "strike",
                "delta",
                "gamma",
                "vega",
                "theta",
                "implied_volatility",
            ],
        ),
        ("Rate", &["value", "mark_price"]),
        (
            "Ticker24h",
            &[
                "last_price",
                "bid_price",
                "bid_quantity",
                "ask_price",
                "ask_quantity",
                "open_price",
                "high_price",
                "low_price",
                "volume_base",
                "volume_quote",
                "price_change_abs",
                "price_change_pct",
                "vwap",
                "mark_price",
            ],
        ),
        (
            "MarkPrice",
            &[
                "mark_price",
                "index_price",
                "estimated_settlement_price",
                "funding_rate",
            ],
        ),
        (
            "IndexPrice",
            &[
                "spot_index_price",
                "contract_index_price",
                "index_price",
                "funding_rate",
            ],
        ),
        ("FundingRate", &["funding_rate"]),
        (
            "OpenInterest",
            &["contracts", "quote_value", "change_24h", "change_pct_24h"],
        ),
    ];
    for (kind, fields) in decimal_fields {
        if let Some(payload) = object
            .get_mut(*kind)
            .and_then(serde_json::Value::as_object_mut)
        {
            for field in *fields {
                if let Some(decimal) = payload.get_mut(*field) {
                    if !decimal.is_null() {
                        *decimal = fixed_decimal_string(decimal)?;
                    }
                }
            }
        }
    }
    Ok(())
}

fn fixed_decimal_string(value: &serde_json::Value) -> Result<serde_json::Value, String> {
    value
        .as_str()
        .map(|_| value.clone())
        .ok_or_else(|| "market observation decimal must be a string".to_string())
}

impl MarketSnapshotPublisher for MmapMarketSnapshotPublisher {
    fn publish(
        &mut self,
        snapshot: &crate::domain::snapshot::MarketSnapshot,
    ) -> Result<(), String> {
        Self::publish(self, snapshot)
    }
}
fn attach_configured_market_source(
    runtime: &mut MarketApplication,
    credentials_root: &std::path::Path,
    source_id: &str,
    binding: &WorkspaceMarketSourceBinding,
) -> Result<(), String> {
    sources::attach_configured(runtime, credentials_root, source_id, binding)
}

/// Canonical endpoint defaults shared by the one-shot CLI and Market server.
pub fn default_endpoint(provider: &str) -> &'static str {
    match provider {
        "binance-spot-websocket" => "wss://stream.binance.com:9443/ws",
        "binance-equity" => "https://api.binance.com",
        "binance-usdm-futures-websocket" => "wss://fstream.binance.com/ws",
        "binance-coinm-futures-websocket" => "wss://dstream.binance.com/ws",
        "binance-usdm-futures-rest" => "https://fapi.binance.com",
        "binance-coinm-futures-rest" => "https://dapi.binance.com",
        "binance-options-rest" => "https://eapi.binance.com",
        // Binance Options market streams are served by the futures stream
        // gateway. The Options-specific host currently returns 404.
        "binance-options-websocket" => "wss://fstream.binance.com/ws",
        "okx-spot-rest" | "okx-swap-rest" | "okx-futures-rest" | "okx-options-rest" => {
            "https://www.okx.com"
        }
        "okx-public-websocket" => "wss://ws.okx.com:8443/ws/v5/public",
        "massive-equity-websocket" => "http://socket.massiveprivateserver.site/stocks",
        "massive-options-websocket" => "http://socket.massiveprivateserver.site/options",
        "hyperliquid-info" => "https://api.hyperliquid.xyz/info",
        "hyperliquid-websocket" => "wss://api.hyperliquid.xyz/ws",
        _ => "https://api.binance.com",
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::default_endpoint;

    #[test]
    fn binance_spot_websocket_uses_a_websocket_endpoint() {
        assert_eq!(
            default_endpoint("binance-spot-websocket"),
            "wss://stream.binance.com:9443/ws"
        );
    }
}

pub(super) fn attach_stream<
    C: kairos_integration::application::AsyncMarketEventSource + 'static,
>(
    runtime: &mut MarketApplication,
    source_id: &str,
    exchange: &str,
    market_type: &str,
    asset_type: &str,
    connection: C,
) -> Result<(), String> {
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new(exchange).map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let input_capacity = runtime.source_input_capacity();
    runtime.attach_source(spawn_stream(descriptor, connection, input_capacity))
}

fn attach_binance_stream(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    asset_type: &str,
    connection: binance::BinanceAsyncMarket,
) -> Result<(), String> {
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new("binance").map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let input_capacity = runtime.source_input_capacity();
    runtime.attach_source(spawn_binance(descriptor, connection, input_capacity))
}

pub(super) fn attach_binance_snapshot<C>(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    asset_type: &str,
    connection: C,
    interval: std::time::Duration,
) -> Result<(), String>
where
    C: kairos_integration::application::AsyncMarketSnapshotConnection + 'static,
{
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new("binance").map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let input_capacity = runtime.source_input_capacity();
    runtime.attach_source(spawn_snapshot(
        descriptor,
        connection,
        interval,
        input_capacity,
    ))
}

pub fn attach_okx_snapshot_source(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    asset_type: &str,
    instrument_type: OkxInstrumentType,
    endpoint: impl Into<String>,
    interval: std::time::Duration,
) -> Result<(), String> {
    let provider = OkxConnection::connect(OkxConnectionConfig {
        environment: "public".into(),
        rest_base_url: endpoint.into(),
        shared_quota: None,
    })
    .map_err(|error| error.to_string())?;
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new("okx").map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let handle = spawn_snapshot(
        descriptor,
        provider.market_snapshot(instrument_type),
        interval,
        runtime.source_input_capacity(),
    );
    runtime.attach_source(handle)
}

pub fn attach_okx_live_source(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    let provider = OkxConnection::connect(OkxConnectionConfig {
        environment: "public".into(),
        rest_base_url: "https://www.okx.com".into(),
        shared_quota: None,
    })
    .map_err(|error| error.to_string())?;
    attach_stream(
        runtime,
        source_id,
        "okx",
        market_type,
        "crypto",
        provider
            .live_market(endpoint)
            .map_err(|error| error.to_string())?,
    )
}

pub fn attach_hyperliquid_snapshot_source(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    endpoint: impl Into<String>,
    interval: std::time::Duration,
) -> Result<(), String> {
    let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
        environment: "public".into(),
        info_endpoint: endpoint.into(),
    })
    .map_err(|error| error.to_string())?;
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new("hyperliquid").map_err(|error| error.to_string())?,
        market_type,
        Some("crypto".into()),
    )?;
    let handle = spawn_snapshot(
        descriptor,
        provider.market_snapshot(),
        interval,
        runtime.source_input_capacity(),
    );
    runtime.attach_source(handle)
}

pub fn attach_hyperliquid_live_source(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
        environment: "public".into(),
        info_endpoint: "https://api.hyperliquid.xyz/info".into(),
    })
    .map_err(|error| error.to_string())?;
    attach_stream(
        runtime,
        source_id,
        "hyperliquid",
        market_type,
        "crypto",
        provider
            .live_market(endpoint)
            .map_err(|error| error.to_string())?,
    )
}

/// Attach the async-first Massive live source to the Actor input channel.
pub fn attach_massive_market_source(
    runtime: &mut MarketApplication,
    product: MarketProduct,
    api_key: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    let (source_id, market_type) = match product {
        MarketProduct::Equity => ("massive.public.websocket.equity", "equity"),
        MarketProduct::Options => ("massive.public.websocket.options", "options"),
        _ => return Err("Massive market source requires equity or options product".into()),
    };
    attach_massive_source_with_id(
        runtime,
        source_id,
        "massive",
        market_type,
        "equity",
        product,
        api_key,
        endpoint,
    )
}

#[allow(clippy::too_many_arguments)]
fn attach_massive_source_with_id(
    runtime: &mut MarketApplication,
    source_id: &str,
    exchange: &str,
    route_market_type: &str,
    asset_type: &str,
    product: MarketProduct,
    api_key: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<(), String> {
    let market_type = match product {
        MarketProduct::Equity => MassiveMarketType::Equity,
        MarketProduct::Options => MassiveMarketType::Option,
        _ => return Err("Massive market source requires equity or options product".into()),
    };
    let endpoint = endpoint.into();
    let provider = MassiveConnection::connect(MassiveConnectionConfig {
        environment: "public".into(),
        rest_base_url: endpoint.clone(),
        api_key: secrecy::SecretString::new(api_key.into().into()),
    })
    .map_err(|error| error.to_string())?;
    let connection = provider
        .live_market(
            market_type,
            endpoint,
            kairos_integration::participants::massive::MassiveChannelConfig {
                event_queue_capacity: 4_096,
            },
        )
        .map_err(|error| error.to_string())?;
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_domain_types::Exchange::new(exchange).map_err(|error| error.to_string())?,
        route_market_type,
        Some(asset_type.into()),
    )?;
    let handle = spawn_stream(descriptor, connection, runtime.source_input_capacity());
    runtime.attach_source(handle)
}

/// Attach deterministic replay to the same wake-driven Actor input path used
/// by live providers.
pub fn attach_replay_source(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observations::MarketObservation>,
) -> Result<(), String> {
    attach_replay(runtime, ReplaySource::new(events))
}

pub fn attach_replay_source_with_checkpoint(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observations::MarketObservation>,
    start_unix_nanos: Option<u64>,
    end_unix_nanos: Option<u64>,
    checkpoint: impl Into<std::path::PathBuf>,
) -> Result<(), String> {
    attach_replay(
        runtime,
        ReplaySource::with_checkpoint(events, start_unix_nanos, end_unix_nanos, checkpoint)?,
    )
}

pub fn attach_replay_source_with_policy(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observations::MarketObservation>,
    start_unix_nanos: Option<u64>,
    end_unix_nanos: Option<u64>,
    checkpoint: impl Into<std::path::PathBuf>,
    clock: MarketReplayClock,
    speed_multiplier: u32,
    start_paused: bool,
) -> Result<(), String> {
    attach_replay(
        runtime,
        ReplaySource::with_policy(
            events,
            start_unix_nanos,
            end_unix_nanos,
            checkpoint,
            clock,
            speed_multiplier,
            start_paused,
        )?,
    )
}

fn attach_replay(runtime: &mut MarketApplication, source: ReplaySource) -> Result<(), String> {
    let descriptor = SourceDescriptor::all_routes(SourceId::new("replay")?);
    let handle = spawn_replay(descriptor, source, runtime.source_input_capacity());
    runtime.attach_source(handle)
}
