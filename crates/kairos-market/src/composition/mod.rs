//! Process composition for concrete provider feeds.

use kairos_integration::application::{
    AccessScope, ConnectionSpec, IntegrationCapability, ProductFamily, TransportKind,
};
use kairos_integration::{Integration, IntegrationRoute};

pub use crate::services::actor::MarketActor;
pub use crate::services::aeron::AeronReferenceChangeSource;
pub use crate::services::composite::{CompositeMarketFeed, MarketFeedFactory, MarketRoute};
pub use crate::services::integration::IntegrationMarketFeed;
pub use crate::services::publication::{
    MmapMarketSnapshotPublisher, MmapOrderBookSnapshotPublisher,
};
pub use crate::services::replay::ReplayMarketFeed;

/// Default market capability used by a workspace that has not declared an
/// explicit connection catalog. The provider choice remains in composition;
/// strategies only declare their market-data intent.
pub fn default_market_feed() -> Result<CompositeMarketFeed, String> {
    let mut factories: std::collections::BTreeMap<MarketRoute, MarketFeedFactory> =
        std::collections::BTreeMap::new();
    factories.insert(
        MarketRoute::with_asset_type("binance", "spot", "crypto"),
        Box::new(|| {
            Ok(Box::new(binance_spot_websocket_feed(
                "wss://stream.binance.com:9443/ws",
            )?)
                as Box<dyn crate::application::protocol::MarketFeed>)
        }),
    );
    CompositeMarketFeed::new(factories)
}

/// Canonical endpoint defaults shared by the one-shot CLI and Market server.
pub fn default_endpoint(provider: &str) -> &'static str {
    match provider {
        "binance-spot-websocket" => "wss://stream.binance.com:9443/ws",
        "binance-usdm-futures-rest" => "https://fapi.binance.com",
        "binance-coinm-futures-rest" => "https://dapi.binance.com",
        "binance-options-rest" => "https://eapi.binance.com",
        "okx-spot-rest" | "okx-swap-rest" | "okx-futures-rest" | "okx-options-rest" => {
            "https://www.okx.com"
        }
        "massive-equity-websocket" => "wss://socket.massiveprivateserver.site/stocks",
        "massive-options-websocket" => "wss://socket.massiveprivateserver.site/options",
        _ => "https://api.binance.com",
    }
}

pub fn binance_spot_rest_feed(
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_binance_spot_rest_market_stream(endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: "market.binance.spot.rest".into(),
            route: IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn binance_spot_websocket_feed(
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_binance_spot_websocket_market_stream(endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: "market.binance.spot.websocket".into(),
            route: IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Public,
            transport: TransportKind::WebSocket,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn binance_equity_rest_feed(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_binance_equity_market_stream(api_key, secret, endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: "market.binance.equity.rest".into(),
            route: IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Equity),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: Some(kairos_integration::domain::AssetType::Equity),
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn binance_derivatives_rest_feed(
    product: ProductFamily,
    endpoint: impl Into<String>,
    path: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_binance_derivatives_market_stream(product, endpoint, path)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: format!("market.binance.{product:?}.rest"),
            route: IntegrationRoute::exchange("binance"),
            product: Some(product),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn okx_market_rest_feed(
    product: ProductFamily,
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_okx_market_stream(product, endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: format!("market.okx.{product:?}.rest"),
            route: IntegrationRoute::exchange("okx"),
            product: Some(product),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}

pub fn massive_market_websocket_feed(
    product: ProductFamily,
    api_key: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_massive_market_stream(product, api_key, endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: format!("market.massive.{product:?}.websocket"),
            route: IntegrationRoute::data_provider("massive"),
            product: Some(product),
            access: AccessScope::Public,
            transport: TransportKind::WebSocket,
            capability: IntegrationCapability::MarketStream,
            credential_id: None,
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    IntegrationMarketFeed::new(connection)
}
