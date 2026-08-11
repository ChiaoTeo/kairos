use crate::services::participants::binance::ConnectionDomain;
use std::collections::BTreeMap;

use serde_json::Value;

use crate::application::capabilities::{
    DecimalValue, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType,
    TimeInForce,
};
use crate::application::{CommandOutcome, IntegrationError, OrderEntryConnection};
use crate::services::transport::http::command_error_outcome;

use super::account::BinanceFuturesAccountClient;

pub struct BinanceFuturesOrderConnection {
    client: BinanceFuturesAccountClient,
}

impl BinanceFuturesOrderConnection {
    pub fn new(
        product: ConnectionDomain,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceFuturesAccountClient::new(product, api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        Ok(Self { client })
    }
}

impl OrderEntryConnection for BinanceFuturesOrderConnection {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        if request.options.post_only == Some(true) && request.order_type != OrderType::Limit {
            return Err(IntegrationError::InvalidRequest(
                "Binance Futures post-only orders require a limit order".into(),
            ));
        }
        let mut params = BTreeMap::from([
            (
                "symbol".into(),
                symbol(request).map_err(IntegrationError::InvalidRequest)?,
            ),
            ("side".into(), side(request.side).into()),
            ("type".into(), order_type(request).into()),
            ("quantity".into(), format_decimal(request.quantity)),
            ("newClientOrderId".into(), request.order_id.to_string()),
        ]);
        if let (OrderType::Limit, Some(price)) = (request.order_type, request.limit_price) {
            params.insert("price".into(), format_decimal(price));
            params.insert("timeInForce".into(), time_in_force(request).into());
        }
        if let Some(value) = request.options.reduce_only {
            params.insert("reduceOnly".into(), value.to_string());
        }
        if let Some(value) = &request.options.position_side {
            params.insert("positionSide".into(), value.clone());
        }
        let payload = match self.client.submit_order(params) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        normalize_order_event(request, &payload)
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
                "symbol".into(),
                symbol(request).map_err(IntegrationError::InvalidRequest)?,
            ),
            ("orderId".into(), remote_order_id.into()),
        ]);
        let payload = match self.client.cancel_order(params) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        let event = OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            remote_order_id: payload
                .get("orderId")
                .map(value_as_string)
                .and_then(|value| kairos_domain_types::RemoteOrderId::new(value).ok())
                .or_else(|| kairos_domain_types::RemoteOrderId::new(remote_order_id).ok()),
            filled_quantity: payload
                .get("executedQty")
                .and_then(Value::as_str)
                .map(decimal_from_str)
                .transpose()
                .map_err(IntegrationError::InvalidPayload)?
                .map(|v| rescale(v, request.quantity.scale))
                .transpose()
                .map_err(IntegrationError::InvalidPayload)?,
            occurred_at_unix_nanos: at_unix_nanos.into(),
            reason: String::new(),
        };
        Ok(CommandOutcome::Confirmed(event))
    }
}

fn normalize_order_event(
    request: &OrderEntryRequest,
    payload: &Value,
) -> Result<OrderEntryEvent, String> {
    let status = match payload.get("status").and_then(Value::as_str).unwrap_or("") {
        "NEW" | "PENDING_NEW" => OrderEntryStatus::Accepted,
        "PARTIALLY_FILLED" => OrderEntryStatus::PartiallyFilled,
        "FILLED" => OrderEntryStatus::Filled,
        "CANCELED" => OrderEntryStatus::Canceled,
        "REJECTED" => OrderEntryStatus::Rejected,
        "EXPIRED" => OrderEntryStatus::Expired,
        _ => OrderEntryStatus::Unknown,
    };
    let filled_quantity = payload
        .get("executedQty")
        .and_then(Value::as_str)
        .map(decimal_from_str)
        .transpose()?
        .map(|v| rescale(v, request.quantity.scale))
        .transpose()?;
    Ok(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status,
        remote_order_id: payload
            .get("orderId")
            .map(value_as_string)
            .and_then(|value| kairos_domain_types::RemoteOrderId::new(value).ok()),
        filled_quantity,
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
        "Binance futures order requires a market_id resolved by Reference".to_string()
    })?;
    let value = value.rsplit(':').next().unwrap_or(value).trim();
    if value.is_empty() {
        return Err("Binance futures order symbol is empty".into());
    }
    Ok(value.replace(['/', '-'], "").to_ascii_uppercase())
}

fn side(value: OrderSide) -> &'static str {
    match value {
        OrderSide::Buy => "BUY",
        OrderSide::Sell => "SELL",
    }
}
fn order_type(request: &OrderEntryRequest) -> &'static str {
    let value = request.order_type;
    match value {
        OrderType::Market => "MARKET",
        OrderType::Limit => "LIMIT",
        OrderType::Stop => "MARKET",
        OrderType::StopLimit => "LIMIT",
    }
}

fn time_in_force(request: &OrderEntryRequest) -> &'static str {
    if request.options.post_only == Some(true) {
        return "GTX";
    }
    match request
        .options
        .time_in_force
        .unwrap_or(TimeInForce::GoodTilCanceled)
    {
        TimeInForce::GoodTilCanceled => "GTC",
        TimeInForce::ImmediateOrCancel => "IOC",
        TimeInForce::FillOrKill => "FOK",
        TimeInForce::Day => "GTC",
    }
}
fn value_as_string(value: &Value) -> String {
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
fn decimal_from_str(value: &str) -> Result<DecimalValue, String> {
    DecimalValue::parse(value)
}
fn rescale(value: DecimalValue, scale: u8) -> Result<DecimalValue, String> {
    value.rescale_exact(scale)
}
