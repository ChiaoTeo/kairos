//! Binance account-domain blocking projections not yet sharing the principal context.

use crate::application::{
    AccountCredentialInspectionConnection, AccountEventStreamConnection,
    AccountMarketProfileConnection, AccountReadConnection, IntegrationError,
};
use crate::services::participants::binance as service;

use super::ConnectionDomain;

fn invalid(error: String) -> IntegrationError {
    IntegrationError::InvalidRequest(error)
}

pub fn blocking_spot_account(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn AccountReadConnection + Send>, IntegrationError> {
    service::spot::account::BinanceSpotAccountConnection::new(api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn AccountReadConnection + Send>)
        .map_err(invalid)
}

pub fn blocking_spot_credential_inspection(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn AccountCredentialInspectionConnection>, IntegrationError> {
    service::spot::account::BinanceSpotAccountConnection::new(api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn AccountCredentialInspectionConnection>)
        .map_err(invalid)
}

pub fn blocking_spot_market_profile(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn AccountMarketProfileConnection + Send>, IntegrationError> {
    service::spot::account::BinanceSpotAccountMarketProfileConnection::new(
        api_key, secret, base_url,
    )
    .map(|value| Box::new(value) as Box<dyn AccountMarketProfileConnection + Send>)
    .map_err(invalid)
}

pub fn blocking_margin_account(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn AccountReadConnection + Send>, IntegrationError> {
    service::margin::BinanceMarginAccountConnection::new(product, api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn AccountReadConnection + Send>)
        .map_err(invalid)
}

pub fn blocking_margin_credential_inspection(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn AccountCredentialInspectionConnection>, IntegrationError> {
    service::margin::BinanceMarginAccountConnection::new(product, api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn AccountCredentialInspectionConnection>)
        .map_err(invalid)
}

pub fn blocking_futures_account(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn AccountReadConnection + Send>, IntegrationError> {
    service::futures::account::BinanceFuturesAccountConnection::new(
        product, api_key, secret, base_url,
    )
    .map(|value| Box::new(value) as Box<dyn AccountReadConnection + Send>)
    .map_err(invalid)
}

pub fn blocking_futures_credential_inspection(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn AccountCredentialInspectionConnection>, IntegrationError> {
    service::futures::account::BinanceFuturesAccountConnection::new(
        product, api_key, secret, base_url,
    )
    .map(|value| Box::new(value) as Box<dyn AccountCredentialInspectionConnection>)
    .map_err(invalid)
}

pub fn blocking_options_account(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn AccountReadConnection + Send>, IntegrationError> {
    service::options::account::BinanceOptionsAccountConnection::new(api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn AccountReadConnection + Send>)
        .map_err(invalid)
}

pub fn blocking_options_credential_inspection(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn AccountCredentialInspectionConnection>, IntegrationError> {
    service::options::account::BinanceOptionsAccountConnection::new(api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn AccountCredentialInspectionConnection>)
        .map_err(invalid)
}

pub fn blocking_spot_account_stream(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
    websocket_endpoint: impl Into<String>,
    segment_key: impl Into<String>,
) -> Result<Box<dyn AccountEventStreamConnection>, IntegrationError> {
    service::spot::user_stream::BinanceSpotAccountStreamConnection::new(
        api_key,
        secret,
        base_url,
        websocket_endpoint,
        segment_key,
    )
    .map(|value| Box::new(value) as Box<dyn AccountEventStreamConnection>)
    .map_err(invalid)
}

pub fn blocking_margin_account_stream(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
    websocket_endpoint: impl Into<String>,
    segment_key: impl Into<String>,
) -> Result<Box<dyn AccountEventStreamConnection>, IntegrationError> {
    service::spot::user_stream::BinanceSpotAccountStreamConnection::new_for_product(
        product,
        api_key,
        secret,
        base_url,
        websocket_endpoint,
        segment_key,
    )
    .map(|value| Box::new(value) as Box<dyn AccountEventStreamConnection>)
    .map_err(invalid)
}

pub fn blocking_futures_account_stream(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
    websocket_endpoint: impl Into<String>,
    segment_key: impl Into<String>,
) -> Result<Box<dyn AccountEventStreamConnection>, IntegrationError> {
    service::futures::account::BinanceFuturesAccountStreamConnection::new(
        product,
        api_key,
        secret,
        base_url,
        websocket_endpoint,
        segment_key,
    )
    .map(|value| Box::new(value) as Box<dyn AccountEventStreamConnection>)
    .map_err(invalid)
}
