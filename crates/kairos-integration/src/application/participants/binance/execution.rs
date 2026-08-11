//! Binance execution-domain blocking projections.

use crate::application::{IntegrationError, OrderQueryConnection};
use crate::services::participants::binance as service;

use super::ConnectionDomain;

fn invalid(error: String) -> IntegrationError {
    IntegrationError::InvalidRequest(error)
}

pub fn blocking_order_query(
    product: ConnectionDomain,
    api_key: impl Into<String>,
    secret: impl Into<String>,
    base_url: impl Into<String>,
) -> Result<Box<dyn OrderQueryConnection>, IntegrationError> {
    if matches!(
        product,
        ConnectionDomain::UsdMFutures | ConnectionDomain::CoinMFutures | ConnectionDomain::Options
    ) {
        return Err(IntegrationError::UnsupportedOperation);
    }
    service::order_query::BinanceOrderQueryConnection::new(product, api_key, secret, base_url)
        .map(|value| Box::new(value) as Box<dyn OrderQueryConnection>)
        .map_err(invalid)
}
