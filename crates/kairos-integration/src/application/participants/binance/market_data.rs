//! Binance public market-data projections.

use crate::application::{
    HistoricalMarketDataConnection, IntegrationError, MarketStreamConnection,
};
use crate::services::participants::binance as service;

use super::ConnectionDomain;

pub fn blocking_spot_rest_market(
    endpoint: impl Into<String>,
) -> Result<Box<dyn MarketStreamConnection>, IntegrationError> {
    service::spot::market_stream::rest_market_stream(endpoint)
        .map(|value| Box::new(value) as Box<dyn MarketStreamConnection>)
}

pub fn blocking_spot_historical_market(
    endpoint: impl Into<String>,
) -> Result<Box<dyn HistoricalMarketDataConnection>, IntegrationError> {
    service::spot::market_stream::BinanceSpotSnapshotReader::new(endpoint)
        .map(|value| Box::new(value) as Box<dyn HistoricalMarketDataConnection>)
}

pub fn blocking_spot_websocket_market(
    endpoint: impl Into<String>,
) -> Result<Box<dyn MarketStreamConnection>, IntegrationError> {
    service::spot::websocket::BinanceSpotWebSocketMarketStream::new(endpoint)
        .map(|value| Box::new(value) as Box<dyn MarketStreamConnection>)
}

pub fn blocking_equity_rest_market(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<Box<dyn MarketStreamConnection>, IntegrationError> {
    service::equity::market_stream::rest_market_stream(api_key, secret, endpoint)
        .map(|value| Box::new(value) as Box<dyn MarketStreamConnection>)
}

pub fn blocking_derivatives_rest_market(
    product: ConnectionDomain,
    endpoint: impl Into<String>,
    path: impl Into<String>,
) -> Result<Box<dyn MarketStreamConnection>, IntegrationError> {
    service::market_data::derivatives::rest_market_stream(endpoint, path, product)
        .map(|value| Box::new(value) as Box<dyn MarketStreamConnection>)
}

pub fn blocking_options_websocket_market(
    endpoint: impl Into<String>,
) -> Result<Box<dyn MarketStreamConnection>, IntegrationError> {
    service::market_data::derivatives::websocket_market_stream(endpoint)
        .map(|value| Box::new(value) as Box<dyn MarketStreamConnection>)
}
