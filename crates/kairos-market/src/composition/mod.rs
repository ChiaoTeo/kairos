//! Process composition for concrete provider feeds.

use kairos_integration::application::{
    AccessScope, ConnectionSpec, IntegrationCapability, ProductFamily, TransportKind,
};
use kairos_integration::Integration;

pub use crate::services::actor::MarketActor;
pub use crate::services::aeron::AeronReferenceChangeSource;
pub use crate::services::integration::IntegrationMarketFeed;
pub use crate::services::publication::{
    MmapMarketSnapshotPublisher, MmapOrderBookSnapshotPublisher,
};
pub use crate::services::replay::ReplayMarketFeed;

pub fn binance_spot_rest_feed(
    endpoint: impl Into<String>,
) -> Result<IntegrationMarketFeed, String> {
    let integration = Integration::new()
        .with_binance_spot_rest_market_stream(endpoint)
        .map_err(|error| error.to_string())?;
    let connection = integration
        .connect_market_stream(&ConnectionSpec {
            connection_id: "market.binance.spot.rest".into(),
            provider: "binance".into(),
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
            provider: "binance".into(),
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
            provider: "binance".into(),
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
