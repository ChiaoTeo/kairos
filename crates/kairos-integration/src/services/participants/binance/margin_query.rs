//! Async Binance cross/isolated margin order queries.

use crate::application::{ExternalOrder, ExternalOrderQuery, IntegrationError};

use super::order_query::{normalize_many, normalize_one, params};
use super::spot::account::BinanceSpotAccountClient;
use super::ConnectionDomain;

pub(crate) struct BinanceMarginOrderQueryConnection {
    client: BinanceSpotAccountClient,
    product: ConnectionDomain,
    isolated_symbol: Option<String>,
}

impl BinanceMarginOrderQueryConnection {
    pub(crate) fn new(
        client: BinanceSpotAccountClient,
        product: ConnectionDomain,
        isolated_symbol: Option<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ConnectionDomain::CrossMargin | ConnectionDomain::IsolatedMargin
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance margin query requires cross or isolated margin".into(),
            ));
        }
        if product == ConnectionDomain::IsolatedMargin && isolated_symbol.is_none() {
            return Err(IntegrationError::InvalidRequest(
                "Binance isolated-margin query requires a route symbol".into(),
            ));
        }
        Ok(Self {
            client,
            product,
            isolated_symbol,
        })
    }

    fn params(
        &self,
        query: &ExternalOrderQuery,
    ) -> Result<std::collections::BTreeMap<String, String>, IntegrationError> {
        let mut values = params(query);
        if self.product == ConnectionDomain::IsolatedMargin {
            values.insert("isIsolated".into(), "TRUE".into());
            let expected = self
                .isolated_symbol
                .as_ref()
                .expect("validated isolated symbol");
            if let Some(actual) = values.get("symbol") {
                if actual != expected {
                    return Err(IntegrationError::InvalidRequest(format!(
                        "isolated-margin query symbol {actual} does not match route symbol {expected}"
                    )));
                }
            } else {
                values.insert("symbol".into(), expected.clone());
            }
        }
        Ok(values)
    }

    pub(crate) async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let payload = self
            .client
            .signed_get_async("/sapi/v1/margin/openOrders", self.params(query)?)
            .await
            .map_err(map_error)?;
        normalize_many(&payload).map_err(IntegrationError::InvalidPayload)
    }

    pub(crate) async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        if query.symbol.is_none() && self.isolated_symbol.is_none() {
            return Err(IntegrationError::InvalidRequest(
                "Binance margin order history requires a provider symbol".into(),
            ));
        }
        let payload = self
            .client
            .signed_get_async("/sapi/v1/margin/allOrders", self.params(query)?)
            .await
            .map_err(map_error)?;
        normalize_many(&payload).map_err(IntegrationError::InvalidPayload)
    }

    pub(crate) async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let order_id = query
            .order_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| IntegrationError::InvalidRequest("order_id is required".into()))?;
        let mut values = self.params(query)?;
        values.insert("orderId".into(), order_id.into());
        let payload = self
            .client
            .signed_get_async("/sapi/v1/margin/order", values)
            .await
            .map_err(map_error)?;
        if payload.is_null() {
            Ok(None)
        } else {
            normalize_one(&payload)
                .map(Some)
                .map_err(IntegrationError::InvalidPayload)
        }
    }
}

fn map_error(error: crate::services::transport::http::ExchangeError) -> IntegrationError {
    use crate::services::transport::http::ExchangeError;
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::LocalRateLimit { message, .. } => IntegrationError::RateLimited(message),
        ExchangeError::Http { status: 401, body } => IntegrationError::Authentication(body),
        ExchangeError::Http { status: 403, body } => IntegrationError::Authorization(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isolated_query_injects_and_enforces_route_symbol() {
        let client = BinanceSpotAccountClient::new("key", "secret", "http://127.0.0.1:1").unwrap();
        let connection = BinanceMarginOrderQueryConnection::new(
            client,
            ConnectionDomain::IsolatedMargin,
            Some("BTCUSDT".into()),
        )
        .unwrap();
        let values = connection.params(&ExternalOrderQuery::default()).unwrap();
        assert_eq!(values.get("symbol").map(String::as_str), Some("BTCUSDT"));
        assert_eq!(values.get("isIsolated").map(String::as_str), Some("TRUE"));

        let error = connection
            .params(&ExternalOrderQuery {
                symbol: Some(kairos_domain_types::Symbol::new("ETHUSDT").unwrap()),
                ..Default::default()
            })
            .unwrap_err();
        assert!(matches!(error, IntegrationError::InvalidRequest(_)));
    }
}
