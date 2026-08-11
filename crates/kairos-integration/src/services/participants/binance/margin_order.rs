//! Native Binance margin order-entry connection.

use crate::services::participants::binance::ConnectionDomain;
use std::collections::BTreeMap;

use crate::application::capabilities::{OrderEntryEvent, OrderEntryRequest};
use crate::application::{CommandOutcome, IntegrationError, OrderEntryConnection};
use crate::services::transport::http::command_error_outcome;

use super::spot::account::BinanceSpotAccountClient;
use super::spot::order;

pub struct BinanceMarginOrderConnection {
    client: BinanceSpotAccountClient,
    product: ConnectionDomain,
}

impl BinanceMarginOrderConnection {
    pub fn new(
        product: ConnectionDomain,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        if !matches!(
            product,
            ConnectionDomain::CrossMargin | ConnectionDomain::IsolatedMargin
        ) {
            return Err("Binance margin order entry requires cross or isolated margin".into());
        }
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        Ok(Self { client, product })
    }
}

impl OrderEntryConnection for BinanceMarginOrderConnection {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        if request.options.post_only == Some(true)
            && request.order_type != crate::application::capabilities::OrderType::Limit
        {
            return Err(IntegrationError::InvalidRequest(
                "Binance Margin post-only orders require a limit order".into(),
            ));
        }
        let mut params = BTreeMap::from([
            (
                "symbol".into(),
                order::symbol(request).map_err(IntegrationError::InvalidRequest)?,
            ),
            ("side".into(), order::side(request.side).into()),
            ("type".into(), order::order_type(request).into()),
            ("quantity".into(), order::format_decimal(request.quantity)),
            ("newClientOrderId".into(), request.order_id.to_string()),
            ("newOrderRespType".into(), "RESULT".into()),
        ]);
        if self.product == ConnectionDomain::IsolatedMargin {
            params.insert("isIsolated".into(), "TRUE".into());
        }
        if let (crate::application::capabilities::OrderType::Limit, Some(price)) =
            (request.order_type, request.limit_price)
        {
            params.insert("price".into(), order::format_decimal(price));
            params.insert(
                "timeInForce".into(),
                match request.options.time_in_force {
                    Some(crate::application::capabilities::TimeInForce::ImmediateOrCancel) => "IOC",
                    Some(crate::application::capabilities::TimeInForce::FillOrKill) => "FOK",
                    _ => "GTC",
                }
                .into(),
            );
        }
        let payload = match self.client.signed_post("/sapi/v1/margin/order", params) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        order::normalize_order_event(request, &payload)
            .map(CommandOutcome::Confirmed)
            .map_err(IntegrationError::InvalidPayload)
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        _at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        if remote_order_id.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "exchange order id is required for cancellation".into(),
            ));
        }
        let mut params = BTreeMap::from([
            (
                "symbol".into(),
                order::symbol(request).map_err(IntegrationError::InvalidRequest)?,
            ),
            ("orderId".into(), remote_order_id.into()),
        ]);
        if self.product == ConnectionDomain::IsolatedMargin {
            params.insert("isIsolated".into(), "TRUE".into());
        }
        let payload = match self.client.signed_delete("/sapi/v1/margin/order", params) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        order::normalize_order_event(request, &payload)
            .map(CommandOutcome::Confirmed)
            .map_err(IntegrationError::InvalidPayload)
    }
}
