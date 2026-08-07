use std::collections::BTreeMap;

use serde_json::Value;

use crate::application::{Connection, ConnectionSpec, OrderEntryConnection};
use crate::domain::{
    AccessScope, DecimalValue, IntegrationCapability, OrderEntryEvent, OrderEntryRequest,
    OrderEntryStatus, OrderSide, OrderType, ProductFamily, TimeInForce, TransportKind,
};
use crate::services::connections::ManagedConnection;

use super::account::BinanceFuturesAccountClient;

pub struct BinanceFuturesOrderConnection {
    connection: ManagedConnection,
    client: BinanceFuturesAccountClient,
}

impl BinanceFuturesOrderConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceFuturesAccountClient::new(product, api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: format!("execution.binance.{}.rest", product_name(product)),
            provider: "binance".into(),
            product: Some(product),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::OrderEntry,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
        })
    }
}

impl Connection for BinanceFuturesOrderConnection {
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

impl OrderEntryConnection for BinanceFuturesOrderConnection {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        self.start()?;
        if request.options.post_only == Some(true) && request.order_type != OrderType::Limit {
            return Err("Binance Futures post-only orders require a limit order".into());
        }
        let mut params = BTreeMap::from([
            ("symbol".into(), symbol(request)?),
            ("side".into(), side(request.side).into()),
            ("type".into(), order_type(request).into()),
            ("quantity".into(), format_decimal(request.quantity)),
            ("newClientOrderId".into(), request.order_id.clone()),
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
                .map(|v| rescale(v, request.quantity.scale))
                .transpose()?,
            occurred_at_unix_nanos: at_unix_nanos,
            reason: String::new(),
        })
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
        venue_order_id: payload.get("orderId").map(value_as_string),
        filled_quantity,
        occurred_at_unix_nanos: 0,
        reason: payload
            .get("msg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}

fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    let value = request
        .market_id
        .as_deref()
        .or_else(|| request.instrument_id.strip_prefix("instrument:binance:"))
        .or_else(|| request.instrument_id.strip_prefix("instrument:"))
        .ok_or_else(|| {
            "Binance futures order requires a market_id or Binance instrument id".to_string()
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
fn product_name(value: ProductFamily) -> &'static str {
    match value {
        ProductFamily::UsdMFutures => "usd-m-futures",
        ProductFamily::CoinMFutures => "coin-m-futures",
        _ => "futures",
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
    Ok(DecimalValue::new(
        value
            .mantissa
            .checked_mul(10_i64.pow((scale - value.scale) as u32))
            .ok_or_else(|| "decimal rescale overflow".to_string())?,
        scale,
    ))
}
