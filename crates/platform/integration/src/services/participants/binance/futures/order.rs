use std::collections::BTreeMap;

use serde_json::Value;

use crate::application::capabilities::{
    DecimalValue, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType,
    TimeInForce,
};
use crate::application::{CommandOutcome, IndeterminateCommand, IntegrationError};
use crate::services::transport::http::command_error_outcome;

use super::account::BinanceFuturesAccountClient;

pub struct BinanceFuturesOrderConnection {
    client: BinanceFuturesAccountClient,
}

impl BinanceFuturesOrderConnection {
    pub(crate) fn from_client(client: BinanceFuturesAccountClient) -> Self {
        Self { client }
    }

    pub(crate) async fn submit_order_async(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let params = submit_params(request)?;
        let payload = match tokio::time::timeout(
            std::time::Duration::from_secs(10),
            self.client.submit_order_async(params),
        )
        .await
        {
            Ok(Ok(payload)) => payload,
            Ok(Err(error)) => return command_error_outcome(error),
            Err(_) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent("Binance Futures submit timed out"),
                ))
            }
        };
        normalize_order_event(request, &payload)
            .map(CommandOutcome::Confirmed)
            .map_err(IntegrationError::InvalidPayload)
    }

    pub(crate) async fn cancel_order_async(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let params = cancel_params(request, remote_order_id)?;
        let payload = match tokio::time::timeout(
            std::time::Duration::from_secs(10),
            self.client.cancel_order_async(params),
        )
        .await
        {
            Ok(Ok(payload)) => payload,
            Ok(Err(error)) => return command_error_outcome(error),
            Err(_) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent("Binance Futures cancel timed out"),
                ))
            }
        };
        normalize_cancel_event(request, remote_order_id, at_unix_nanos, &payload)
    }
}

fn submit_params(
    request: &OrderEntryRequest,
) -> Result<BTreeMap<String, String>, IntegrationError> {
    if matches!(request.order_type, OrderType::Stop | OrderType::StopLimit) {
        return Err(IntegrationError::UnsupportedOperation);
    }
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
        ("newOrderRespType".into(), "RESULT".into()),
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
    Ok(params)
}

fn cancel_params(
    request: &OrderEntryRequest,
    remote_order_id: &str,
) -> Result<BTreeMap<String, String>, IntegrationError> {
    if remote_order_id.trim().is_empty() {
        return Err(IntegrationError::InvalidRequest(
            "exchange order id is required for cancellation".into(),
        ));
    }
    Ok(BTreeMap::from([
        (
            "symbol".into(),
            symbol(request).map_err(IntegrationError::InvalidRequest)?,
        ),
        ("orderId".into(), remote_order_id.into()),
    ]))
}

fn normalize_cancel_event(
    request: &OrderEntryRequest,
    remote_order_id: &str,
    at_unix_nanos: u64,
    payload: &Value,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    Ok(CommandOutcome::Confirmed(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status: OrderEntryStatus::Canceled,
        remote_order_id: payload
            .get("orderId")
            .map(value_as_string)
            .and_then(|value| kairos_primitives::RemoteOrderId::new(value).ok())
            .or_else(|| kairos_primitives::RemoteOrderId::new(remote_order_id).ok()),
        filled_quantity: payload
            .get("executedQty")
            .and_then(Value::as_str)
            .map(decimal_from_str)
            .transpose()
            .map_err(IntegrationError::InvalidPayload)?
            .map(|value| rescale(value, request.quantity.scale))
            .transpose()
            .map_err(IntegrationError::InvalidPayload)?,
        occurred_at_unix_nanos: at_unix_nanos.into(),
        reason: String::new(),
    }))
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
            .and_then(|value| kairos_primitives::RemoteOrderId::new(value).ok()),
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
    if request.provider_instrument.participant.id != "binance" {
        return Err(format!(
            "Binance futures order requires a Binance provider instrument, got {}",
            request.provider_instrument.participant.id
        ));
    }
    Ok(request.provider_instrument.source_symbol.to_string())
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
        OrderType::Stop | OrderType::StopLimit => {
            unreachable!("stop orders are rejected until OrderEntryRequest carries trigger price")
        }
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
