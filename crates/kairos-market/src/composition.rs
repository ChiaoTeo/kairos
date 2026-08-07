//! Process composition for concrete provider feeds.

use kairos_integration::application::{
    AccessScope, ConnectionSpec, IntegrationCapability, ProductFamily, TransportKind,
};
use kairos_integration::Integration;

use crate::services::integration::IntegrationMarketFeed;

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
