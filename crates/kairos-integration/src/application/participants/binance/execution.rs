//! Binance execution-domain blocking projections.

use crate::application::{IntegrationError, OrderEntryConnection, OrderQueryConnection};
use crate::services::participants::binance as service;

use super::ConnectionDomain;

fn invalid(error: String) -> IntegrationError {
    IntegrationError::InvalidRequest(error)
}

pub fn blocking_margin_order_entry(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn OrderEntryConnection>, IntegrationError> {
    service::margin_order::BinanceMarginOrderConnection::new(product, api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn OrderEntryConnection>)
        .map_err(invalid)
}

pub fn blocking_futures_order_entry(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn OrderEntryConnection>, IntegrationError> {
    service::futures::order::BinanceFuturesOrderConnection::new(product, api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn OrderEntryConnection>)
        .map_err(invalid)
}

pub fn blocking_options_order_entry(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn OrderEntryConnection>, IntegrationError> {
    service::options::order::BinanceOptionsOrderConnection::new(api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn OrderEntryConnection>)
        .map_err(invalid)
}

pub fn blocking_equity_order_entry(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn OrderEntryConnection>, IntegrationError> {
    service::equity::order::BinanceEquityOrderConnection::new(api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn OrderEntryConnection>)
        .map_err(invalid)
}

pub fn blocking_order_query(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn OrderQueryConnection>, IntegrationError> {
    service::order_query::BinanceOrderQueryConnection::new(product, api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn OrderQueryConnection>)
        .map_err(invalid)
}

pub fn blocking_equity_order_query(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn OrderQueryConnection>, IntegrationError> {
    service::equity::query::BinanceEquityOrderQueryConnection::new(api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn OrderQueryConnection>)
        .map_err(invalid)
}
