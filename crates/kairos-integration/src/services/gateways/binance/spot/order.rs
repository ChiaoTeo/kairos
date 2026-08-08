//! Binance Spot order-entry connection.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::application::{Connection, ConnectionSpec, OrderEntryConnection};
use crate::domain::{
    AccessScope, DecimalValue, IntegrationCapability, OrderEntryEvent, OrderEntryRequest,
    OrderEntryStatus, OrderSide, OrderType, ProductFamily, TimeInForce, TransportKind,
};
use crate::services::connections::ManagedConnection;

use super::account::BinanceSpotAccountClient;

pub struct BinanceSpotOrderConnection {
    connection: ManagedConnection,
    client: BinanceSpotAccountClient,
}

impl BinanceSpotOrderConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: "execution.binance.spot.rest".into(),
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::OrderEntry,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        let connection = ManagedConnection::new(spec, Vec::new())?;
        Ok(Self { connection, client })
    }
}

impl Connection for BinanceSpotOrderConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.connection.reconnect()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}

impl OrderEntryConnection for BinanceSpotOrderConnection {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        self.start()?;
        if request.options.post_only == Some(true) && request.order_type != OrderType::Limit {
            return Err("Binance Spot post-only orders require a limit order".into());
        }
        let mut params = BTreeMap::from([
            ("symbol".into(), symbol(request)?),
            ("side".into(), side(request.side).into()),
            ("type".into(), order_type(request).into()),
            ("quantity".into(), decimal(request.quantity)),
            ("newClientOrderId".into(), request.order_id.clone()),
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
        let payload = self
            .client
            .submit_order(params)
            .map_err(|error| error.to_string())?;
        normalize_order_event(request, &payload)
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        venue_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<OrderEntryEvent, String> {
        self.start()?;
        if venue_order_id.trim().is_empty() {
            return Err("venue order id is required for cancellation".into());
        }
        let params = BTreeMap::from([
            ("symbol".into(), symbol(request)?),
            ("orderId".into(), venue_order_id.into()),
        ]);
        let payload = self
            .client
            .cancel_order(params)
            .map_err(|error| error.to_string())?;
        Ok(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            venue_order_id: payload
                .get("orderId")
                .map(value_as_string)
                .or_else(|| Some(venue_order_id.into())),
            filled_quantity: payload
                .get("executedQty")
                .and_then(Value::as_str)
                .map(decimal_from_str)
                .transpose()?
                .map(|value| rescale(value, request.quantity.scale))
                .transpose()?,
            occurred_at_unix_nanos: at_unix_nanos.max(now_nanos()),
            reason: String::new(),
        })
    }
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
        venue_order_id: payload.get("orderId").map(value_as_string),
        filled_quantity,
        occurred_at_unix_nanos: now_nanos(),
        reason: payload
            .get("msg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}

pub(crate) fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    let value = request
        .market_id
        .as_deref()
        .or_else(|| request.instrument_id.strip_prefix("instrument:binance:"))
        .or_else(|| request.instrument_id.strip_prefix("instrument:"))
        .ok_or_else(|| {
            "Binance Spot order requires a market_id or Binance instrument id".to_string()
        })?;
    let value = value.rsplit(':').next().unwrap_or(value).trim();
    if value.is_empty() {
        return Err("Binance Spot order symbol is empty".into());
    }
    Ok(value.replace(['/', '-'], "").to_ascii_uppercase())
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
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let mut parts = unsigned.split('.');
    let whole = parts.next().unwrap_or("0");
    let fraction = parts.next().unwrap_or("");
    if parts.next().is_some()
        || whole.is_empty()
        || !whole.chars().all(|c| c.is_ascii_digit())
        || !fraction.chars().all(|c| c.is_ascii_digit())
        || fraction.len() > 18
    {
        return Err(format!("invalid decimal: {value}"));
    }
    let mut mantissa = format!("{whole}{fraction}")
        .parse::<i64>()
        .map_err(|_| format!("decimal overflow: {value}"))?;
    if negative {
        mantissa = -mantissa;
    }
    Ok(DecimalValue::new(mantissa, fraction.len() as u8))
}

fn rescale(value: DecimalValue, scale: u8) -> Result<DecimalValue, String> {
    if value.scale > scale {
        let divisor = 10_i64.pow((value.scale - scale) as u32);
        if value.mantissa % divisor != 0 {
            return Err("decimal precision exceeds order quantity scale".into());
        }
        return Ok(DecimalValue::new(value.mantissa / divisor, scale));
    }
    let mantissa = value
        .mantissa
        .checked_mul(10_i64.pow((scale - value.scale) as u32))
        .ok_or_else(|| "decimal rescale overflow".to_string())?;
    Ok(DecimalValue::new(mantissa, scale))
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
    use super::normalize_order_event;
    use crate::domain::{
        DecimalValue, OrderEntryRequest as OrderRequest, OrderEntryStatus as OrderStatus,
        OrderSide, OrderType,
    };

    fn request() -> OrderRequest {
        OrderRequest {
            order_id: "order-1".into(),
            intent_id: None,
            account_id: "main".into(),
            segment_key: "spot".into(),
            instrument_id: "instrument:binance:BTCUSDT".into(),
            market_id: None,
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
        assert_eq!(event.venue_order_id.as_deref(), Some("123"));
        assert_eq!(event.filled_quantity.unwrap().mantissa, 25);
    }
}
