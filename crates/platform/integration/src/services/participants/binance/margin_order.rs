//! Native Binance margin order-entry connection.

use crate::services::participants::binance::ConnectionDomain;
use std::collections::BTreeMap;

use crate::application::capabilities::{OrderEntryEvent, OrderEntryRequest};
use crate::application::{CommandOutcome, IndeterminateCommand, IntegrationError};
use crate::services::transport::http::command_error_outcome;

use super::spot::account::BinanceSpotAccountClient;
use super::spot::order;

pub struct BinanceMarginOrderConnection {
    client: BinanceSpotAccountClient,
    product: ConnectionDomain,
    isolated_symbol: Option<String>,
}

impl BinanceMarginOrderConnection {
    pub(crate) fn from_client(
        client: BinanceSpotAccountClient,
        product: ConnectionDomain,
        isolated_symbol: Option<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ConnectionDomain::CrossMargin | ConnectionDomain::IsolatedMargin
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance margin order entry requires cross or isolated margin".into(),
            ));
        }
        if product == ConnectionDomain::IsolatedMargin && isolated_symbol.is_none() {
            return Err(IntegrationError::InvalidRequest(
                "Binance isolated-margin order entry requires a route symbol".into(),
            ));
        }
        Ok(Self {
            client,
            product,
            isolated_symbol,
        })
    }

    pub(crate) async fn submit_order_async(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let params = self.submit_params(request)?;
        let payload = match tokio::time::timeout(
            std::time::Duration::from_secs(10),
            self.client
                .signed_post_async("/sapi/v1/margin/order", params),
        )
        .await
        {
            Ok(Ok(payload)) => payload,
            Ok(Err(error)) => return command_error_outcome(error),
            Err(_) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent("Binance Margin submit timed out"),
                ))
            }
        };
        order::normalize_order_event(request, &payload)
            .map(CommandOutcome::Confirmed)
            .map_err(IntegrationError::InvalidPayload)
    }

    pub(crate) async fn cancel_order_async(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let params = self.cancel_params(request, remote_order_id)?;
        let payload = match tokio::time::timeout(
            std::time::Duration::from_secs(10),
            self.client
                .signed_delete_async("/sapi/v1/margin/order", params),
        )
        .await
        {
            Ok(Ok(payload)) => payload,
            Ok(Err(error)) => return command_error_outcome(error),
            Err(_) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent("Binance Margin cancel timed out"),
                ))
            }
        };
        order::normalize_order_event(request, &payload)
            .map(CommandOutcome::Confirmed)
            .map_err(IntegrationError::InvalidPayload)
    }

    fn submit_params(
        &self,
        request: &OrderEntryRequest,
    ) -> Result<BTreeMap<String, String>, IntegrationError> {
        if request.options.post_only == Some(true)
            && request.order_type != crate::application::capabilities::OrderType::Limit
        {
            return Err(IntegrationError::InvalidRequest(
                "Binance Margin post-only orders require a limit order".into(),
            ));
        }
        let symbol = self.route_symbol(request)?;
        let mut params = BTreeMap::from([
            ("symbol".into(), symbol),
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
        Ok(params)
    }

    fn cancel_params(
        &self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
    ) -> Result<BTreeMap<String, String>, IntegrationError> {
        if remote_order_id.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "exchange order id is required for cancellation".into(),
            ));
        }
        let symbol = self.route_symbol(request)?;
        let mut params = BTreeMap::from([
            ("symbol".into(), symbol),
            ("orderId".into(), remote_order_id.into()),
        ]);
        if self.product == ConnectionDomain::IsolatedMargin {
            params.insert("isIsolated".into(), "TRUE".into());
        }
        Ok(params)
    }

    fn route_symbol(&self, request: &OrderEntryRequest) -> Result<String, IntegrationError> {
        let actual = order::symbol(request)
            .map_err(IntegrationError::InvalidRequest)?
            .to_ascii_uppercase();
        if let Some(expected) = &self.isolated_symbol {
            if &actual != expected {
                return Err(IntegrationError::InvalidRequest(format!(
                    "isolated-margin order symbol {actual} does not match route symbol {expected}"
                )));
            }
        }
        Ok(actual)
    }
}
