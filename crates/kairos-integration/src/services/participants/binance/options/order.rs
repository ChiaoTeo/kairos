use std::collections::BTreeMap;

use serde_json::Value;

use crate::application::capabilities::{
    DecimalValue, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType,
    TimeInForce,
};
use crate::application::{CommandOutcome, IntegrationError, OrderEntryConnection};
use crate::services::transport::http::command_error_outcome;

use super::account::{BinanceOptionsAccountClient, Method};

pub struct BinanceOptionsOrderConnection {
    client: BinanceOptionsAccountClient,
}

impl BinanceOptionsOrderConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceOptionsAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        Ok(Self { client })
    }
}
impl OrderEntryConnection for BinanceOptionsOrderConnection {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        if request.options.post_only == Some(true) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Options does not support post-only orders".into(),
            ));
        }
        let mut params = BTreeMap::from([
            (
                String::from("symbol"),
                symbol(request).map_err(IntegrationError::InvalidRequest)?,
            ),
            (String::from("side"), side(request.side).into()),
            (String::from("type"), order_type(request.order_type).into()),
            (String::from("quantity"), format_decimal(request.quantity)),
            (String::from("clientOrderId"), request.order_id.to_string()),
        ]);
        if let (OrderType::Limit, Some(price)) = (request.order_type, request.limit_price) {
            params.insert("price".into(), format_decimal(price));
            params.insert(
                "timeInForce".into(),
                match request
                    .options
                    .time_in_force
                    .unwrap_or(TimeInForce::GoodTilCanceled)
                {
                    TimeInForce::GoodTilCanceled | TimeInForce::Day => "GTC",
                    TimeInForce::ImmediateOrCancel => "IOC",
                    TimeInForce::FillOrKill => "FOK",
                }
                .into(),
            );
        }
        let payload = match self.client.request("/eapi/v1/order", params, Method::Post) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        normalize(request, &payload)
            .map(CommandOutcome::Confirmed)
            .map_err(IntegrationError::InvalidPayload)
    }
    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let params = BTreeMap::from([
            (
                String::from("symbol"),
                symbol(request).map_err(IntegrationError::InvalidRequest)?,
            ),
            (String::from("orderId"), remote_order_id.into()),
        ]);
        let payload = match self
            .client
            .request("/eapi/v1/order", params, Method::Delete)
        {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            remote_order_id: payload
                .get("orderId")
                .map(value_string)
                .and_then(|value| kairos_domain_types::RemoteOrderId::new(value).ok())
                .or_else(|| kairos_domain_types::RemoteOrderId::new(remote_order_id).ok()),
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos.into(),
            reason: String::new(),
        }))
    }
}
fn normalize(request: &OrderEntryRequest, payload: &Value) -> Result<OrderEntryEvent, String> {
    let status = match payload.get("status").and_then(Value::as_str).unwrap_or("") {
        "NEW" => OrderEntryStatus::Accepted,
        "PARTIALLY_FILLED" => OrderEntryStatus::PartiallyFilled,
        "FILLED" => OrderEntryStatus::Filled,
        "CANCELED" => OrderEntryStatus::Canceled,
        "REJECTED" => OrderEntryStatus::Rejected,
        "EXPIRED" => OrderEntryStatus::Expired,
        _ => OrderEntryStatus::Unknown,
    };
    Ok(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status,
        remote_order_id: payload
            .get("orderId")
            .map(value_string)
            .and_then(|value| kairos_domain_types::RemoteOrderId::new(value).ok()),
        filled_quantity: None,
        occurred_at_unix_nanos: 0.into(),
        reason: payload
            .get("msg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}
fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    let value = request.market_id.as_deref().ok_or_else(|| {
        "Binance Options order requires a market_id resolved by Reference".to_string()
    })?;
    let value = value.rsplit(':').next().unwrap_or(value).trim();
    if value.is_empty() {
        return Err("Binance Options order requires a symbol".into());
    }
    Ok(value.to_ascii_uppercase())
}
fn side(value: OrderSide) -> &'static str {
    match value {
        OrderSide::Buy => "BUY",
        OrderSide::Sell => "SELL",
    }
}
fn order_type(value: OrderType) -> &'static str {
    match value {
        OrderType::Market => "MARKET",
        OrderType::Limit => "LIMIT",
        OrderType::Stop => "MARKET",
        OrderType::StopLimit => "LIMIT",
    }
}
fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}
fn format_decimal(value: DecimalValue) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.abs().to_string();
    let scale = value.scale as usize;
    let padded = format!(
        "{}{}",
        "0".repeat(scale.saturating_sub(digits.len())),
        digits
    );
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}
