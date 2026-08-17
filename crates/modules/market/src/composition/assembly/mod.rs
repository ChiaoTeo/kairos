//! Process composition for concrete provider feeds.

use crate::domain::source::{SourceDescriptor, SourceId};
use crate::services::source::{
    spawn_snapshot, spawn_stream, spawn_stream_with_policy, StreamFailurePolicy,
};
use crate::MarketApplication;
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
