//! Binance Spot order-entry connection.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::application::capabilities::{
    DecimalValue, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType,
    TimeInForce,
};
use crate::application::{CommandOutcome, IntegrationError, OrderEntryConnection};
use crate::services::transport::http::command_error_outcome;

use super::account::BinanceSpotAccountClient;

pub struct BinanceSpotOrderConnection {
    client: BinanceSpotAccountClient,
}

impl BinanceSpotOrderConnection {
    pub(crate) fn from_client(client: BinanceSpotAccountClient) -> Result<Self, String> {
        Ok(Self { client })
    }

    pub(crate) async fn submit_order_async(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let params = submit_params(request)?;
        let payload = match self.client.submit_order_async(params).await {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
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
        let payload = match self.client.cancel_order_async(params).await {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        normalize_cancel_event(request, remote_order_id, at_unix_nanos, &payload)
    }
}

impl OrderEntryConnection for BinanceSpotOrderConnection {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let params = submit_params(request)?;
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
        let params = cancel_params(request, remote_order_id)?;
        let payload = match self.client.cancel_order(params) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        normalize_cancel_event(request, remote_order_id, at_unix_nanos, &payload)
    }
}

fn submit_params(
    request: &OrderEntryRequest,
) -> Result<BTreeMap<String, String>, IntegrationError> {
    if request.options.post_only == Some(true) && request.order_type != OrderType::Limit {
        return Err(IntegrationError::InvalidRequest(
            "Binance Spot post-only orders require a limit order".into(),
        ));
    }
    let mut params = BTreeMap::from([
        (
            "symbol".into(),
            symbol(request).map_err(IntegrationError::InvalidRequest)?,
        ),
        ("side".into(), side(request.side).into()),
        ("type".into(), order_type(request).into()),
        ("quantity".into(), decimal(request.quantity)),
        ("newClientOrderId".into(), request.order_id.to_string()),
        ("newOrderRespType".into(), "RESULT".into()),
    ]);
    if let (OrderType::Limit, Some(price)) = (request.order_type, request.limit_price) {
        params.insert("price".into(), decimal(price));
        if request.options.post_only != Some(true) {
            params.insert(
                "timeInForce".into(),
                time_in_force(request.options.time_in_force).into(),
            );
        }
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
            .map(|value| rescale(value, request.quantity.scale))
            .transpose()
            .map_err(IntegrationError::InvalidPayload)?,
        occurred_at_unix_nanos: at_unix_nanos.max(now_nanos()).into(),
        reason: String::new(),
    };
    Ok(CommandOutcome::Confirmed(event))
}

pub(crate) fn normalize_order_event(
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
        .map(|value| rescale(value, request.quantity.scale))
        .transpose()?;
    Ok(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status,
        remote_order_id: payload
            .get("orderId")
            .map(value_as_string)
            .and_then(|value| kairos_domain_types::RemoteOrderId::new(value).ok()),
        filled_quantity,
        occurred_at_unix_nanos: now_nanos().into(),
        reason: payload
            .get("msg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}

pub(crate) fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    if request.provider_instrument.participant.id != "binance" {
        return Err(format!(
            "Binance Spot order requires a Binance provider instrument, got {}",
            request.provider_instrument.participant.id
        ));
    }
    Ok(request.provider_instrument.source_symbol.to_string())
}

pub(crate) fn side(value: OrderSide) -> &'static str {
    match value {
        OrderSide::Buy => "BUY",
        OrderSide::Sell => "SELL",
    }
}
pub(crate) fn order_type(request: &OrderEntryRequest) -> &'static str {
    if request.options.post_only == Some(true) {
        return "LIMIT_MAKER";
    }
    let value = request.order_type;
    match value {
        OrderType::Market => "MARKET",
        OrderType::Limit => "LIMIT",
        OrderType::Stop => "MARKET",
        OrderType::StopLimit => "LIMIT",
    }
}

fn time_in_force(value: Option<TimeInForce>) -> &'static str {
    match value.unwrap_or(TimeInForce::GoodTilCanceled) {
        TimeInForce::GoodTilCanceled => "GTC",
        TimeInForce::ImmediateOrCancel => "IOC",
        TimeInForce::FillOrKill => "FOK",
        TimeInForce::Day => "GTC",
    }
}
fn decimal(value: DecimalValue) -> String {
    format_decimal(value)
}

fn decimal_from_str(value: &str) -> Result<DecimalValue, String> {
    DecimalValue::parse(value)
}

fn rescale(value: DecimalValue, scale: u8) -> Result<DecimalValue, String> {
    value.rescale_exact(scale)
}

pub(crate) fn format_decimal(value: DecimalValue) -> String {
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

fn value_as_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use kairos_domain_types::InstrumentId;

    use super::normalize_order_event;
    use crate::application::capabilities::{
        DecimalValue, OrderEntryRequest as OrderRequest, OrderEntryStatus as OrderStatus,
        OrderSide, OrderType,
    };

    fn request() -> OrderRequest {
        OrderRequest {
            order_id: kairos_domain_types::OrderId::new("order-1").unwrap(),
            intent_id: None,
            account_id: kairos_domain_types::AccountId::new("main").unwrap(),
            segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
            instrument_id: InstrumentId::new("instrument:spot:BTC").unwrap(),
            market_id: None,
            provider_instrument: crate::domain::ProviderInstrumentRef::new(
                crate::domain::ParticipantRef::new(
                    crate::domain::ParticipantKind::Exchange,
                    "binance",
                )
                .unwrap(),
                Some(crate::services::participants::binance::ConnectionDomain::Spot.into()),
                "BTCUSDT",
            )
            .unwrap(),
            side: OrderSide::Buy,
            quantity: DecimalValue::new(25, 2),
            order_type: OrderType::Market,
            limit_price: None,
            options: Default::default(),
        }
    }

    #[test]
    fn normalizes_binance_order_response_without_vendor_statuses() {
        let event = normalize_order_event(
            &request(),
            &serde_json::json!({"orderId":123,"status":"FILLED","executedQty":"0.25"}),
        )
        .unwrap();
        assert_eq!(event.status, OrderStatus::Filled);
        assert_eq!(event.remote_order_id.as_deref(), Some("123"));
        assert_eq!(event.filled_quantity.unwrap().mantissa, 25);
    }
}
