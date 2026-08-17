//! Process composition for concrete provider feeds.

use kairos_integration::participants::binance;
use kairos_integration::participants::hyperliquid::{
    HyperliquidConnection, HyperliquidConnectionConfig,
};
use kairos_integration::participants::massive::{
    MarketType as MassiveMarketType, MassiveConnection, MassiveConnectionConfig,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxProviderInstrumentType, OkxConnection, OkxConnectionConfig,
};
use kairos_workspace::Workspace;

use super::config::{
    BinanceDerivativeProduct, BinanceSpotTransport, MarketConfig, MarketReplayClock,
    MarketSourceBinding, MarketSourceBinding as Binding,
};
use super::{config, sources};

use crate::domain::source::{SourceDescriptor, SourceId};
use crate::services::source::{
    spawn_replay, spawn_snapshot, spawn_stream, spawn_stream_with_policy, ReplayClock,
    ReplaySource, SourceActivator, SourceHandle, StreamFailurePolicy,
};
use crate::MarketApplication;

/// Demand-driven source construction for live and paper Market processes.
///
/// The activator contains only immutable workspace/configuration facts. The
/// active source map and all subscription state remain owned by MarketActor.
pub(crate) struct ConfiguredMarketSourceActivator {
    workspace: Workspace,
}

impl ConfiguredMarketSourceActivator {
    pub(crate) fn new(workspace: Workspace) -> Self {
        Self { workspace }
    }
}

impl SourceActivator for ConfiguredMarketSourceActivator {
    fn activate<'a>(
        &'a mut self,
        market: &'a crate::ResolvedMarket,
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
    market: crate::ResolvedMarket,
    source_input_capacity: usize,
) -> Result<SourceHandle, String> {
    let credentials_root = workspace
        .child(&["credentials"])
        .map_err(|error| error.to_string())?;
    let market_config = MarketConfig::load(&workspace)?;
    let configured_route_exists = market_config
        .sources
        .values()
        .any(|binding| binding_matches_market(binding, &market));
    let mut candidates = market_config
        .sources
        .iter()
        .filter(|(id, binding)| {
            binding.enabled()
                && market
                    .source_id
                    .as_ref()
                    .is_none_or(|requested| requested.as_str().eq_ignore_ascii_case(id))
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
        && market.route.provider_id == "binance"
        && market.route.provider_product == "spot"
    {
        candidates.push((
            "binance-spot".into(),
            Binding::BinanceSpot {
                enabled: true,
                transport: BinanceSpotTransport::Websocket,
                endpoint: None,
                snapshot_interval_ms: 1_000,
            },
        ));
    }
    let [(source_id, binding)] = candidates.as_slice() else {
        return Err(if candidates.is_empty() {
            format!(
                "no Market source supports exchange={} market_type={} asset_type={:?}",
                market.exchange_id, market.route.provider_product, market.asset_type
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

fn binding_matches_market(binding: &MarketSourceBinding, market: &crate::ResolvedMarket) -> bool {
    let provider = market.route.provider_id.as_str();
    let provider_product = market.route.provider_product.as_str();
    let (expected_provider, expected_product) = match binding {
        MarketSourceBinding::BinanceSpot { .. } => ("binance", "spot"),
        MarketSourceBinding::BinanceEquity { .. } => ("binance", "equity"),
        MarketSourceBinding::BinanceDerivatives { product, .. } => (
            "binance",
            match product {
                BinanceDerivativeProduct::UsdMFutures => "usd-m-futures",
                BinanceDerivativeProduct::CoinMFutures => "coin-m-futures",
                BinanceDerivativeProduct::Options => "options",
            },
        ),
        MarketSourceBinding::Massive { product, .. } => (
            "massive",
            match product {
                config::MassiveMarketProduct::Equity => "equity",
                config::MassiveMarketProduct::Options => "options",
            },
        ),
        MarketSourceBinding::Okx {
            instrument_type, ..
        } => (
            "okx",
            match instrument_type {
                config::OkxInstrumentType::Spot => "spot",
                config::OkxInstrumentType::Swap => "swap",
                config::OkxInstrumentType::Futures => "futures",
                config::OkxInstrumentType::Options => "options",
            },
        ),
        MarketSourceBinding::Hyperliquid {
            market_type: configured,
            ..
        } => (
            "hyperliquid",
            match configured {
                config::HyperliquidMarketType::Spot => "spot",
                config::HyperliquidMarketType::Perpetual => "perpetual",
            },
        ),
    };
    provider == expected_provider && provider_product == expected_product
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

pub(crate) fn attach_configured_market_source(
    runtime: &mut MarketApplication,
    credentials_root: &std::path::Path,
    source_id: &str,
    binding: &MarketSourceBinding,
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
        _ => "",
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

    #[test]
    fn unknown_endpoint_key_never_falls_back_to_binance() {
        assert_eq!(default_endpoint("future-provider"), "");
    }
}

pub(crate) fn attach_stream<
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
        kairos_primitives::Exchange::new(exchange).map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let input_capacity = runtime.source_input_capacity();
    runtime.attach_source(spawn_stream(descriptor, connection, input_capacity))
}

pub(crate) fn attach_binance_stream(
    runtime: &mut MarketApplication,
    source_id: &str,
    market_type: &str,
    asset_type: &str,
    connection: binance::BinanceAsyncMarket,
) -> Result<(), String> {
    let descriptor = SourceDescriptor::new(
        SourceId::new(source_id)?,
        kairos_primitives::Exchange::new("binance").map_err(|error| error.to_string())?,
        market_type,
        Some(asset_type.into()),
    )?;
    let input_capacity = runtime.source_input_capacity();
    runtime.attach_source(spawn_stream_with_policy(
        descriptor,
        connection,
        input_capacity,
        StreamFailurePolicy::MarketScopedResync,
    ))
}

pub(crate) fn attach_binance_snapshot<C>(
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
        kairos_primitives::Exchange::new("binance").map_err(|error| error.to_string())?,
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
    instrument_type: OkxProviderInstrumentType,
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
        kairos_primitives::Exchange::new("okx").map_err(|error| error.to_string())?,
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
        kairos_primitives::Exchange::new("hyperliquid").map_err(|error| error.to_string())?,
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
pub(crate) fn attach_massive_source_with_id(
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
        kairos_primitives::Exchange::new(exchange).map_err(|error| error.to_string())?,
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
    events: impl IntoIterator<Item = crate::domain::observation::MarketObservation>,
) -> Result<(), String> {
    attach_replay(runtime, ReplaySource::new(events))
}

pub fn attach_replay_source_with_checkpoint(
    runtime: &mut MarketApplication,
    events: impl IntoIterator<Item = crate::domain::observation::MarketObservation>,
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
    events: impl IntoIterator<Item = crate::domain::observation::MarketObservation>,
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
            match clock {
                MarketReplayClock::Maximum => ReplayClock::Maximum,
                MarketReplayClock::EventTime => ReplayClock::EventTime,
            },
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
