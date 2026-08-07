use std::collections::BTreeMap;

use serde_json::Value;

use crate::application::{Connection, ConnectionSpec, OrderEntryConnection};
use crate::domain::{
    AccessScope, DecimalValue, IntegrationCapability, OrderEntryEvent, OrderEntryRequest,
    OrderEntryStatus, OrderSide, OrderType, ProductFamily, TimeInForce, TransportKind,
};
use crate::services::connections::ManagedConnection;

use super::account::{BinanceOptionsAccountClient, Method};

pub struct BinanceOptionsOrderConnection {
    connection: ManagedConnection,
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
        let spec = ConnectionSpec {
            connection_id: "execution.binance.options.rest".into(),
            provider: "binance".into(),
            product: Some(ProductFamily::Options),
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
impl Connection for BinanceOptionsOrderConnection {
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
impl OrderEntryConnection for BinanceOptionsOrderConnection {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        self.start()?;
        if request.options.post_only == Some(true) {
            return Err("Binance Options does not support post-only orders".into());
        }
        let mut params = BTreeMap::from([
            (String::from("symbol"), symbol(request)?),
            (String::from("side"), side(request.side).into()),
            (String::from("type"), order_type(request.order_type).into()),
            (String::from("quantity"), format_decimal(request.quantity)),
            (String::from("clientOrderId"), request.order_id.clone()),
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
        let payload = self
            .client
            .request("/eapi/v1/order", params, Method::Post)
            .map_err(|error| error.to_string())?;
        normalize(request, &payload)
    }
    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        venue_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<OrderEntryEvent, String> {
        self.start()?;
        let params = BTreeMap::from([
            (String::from("symbol"), symbol(request)?),
            (String::from("orderId"), venue_order_id.into()),
        ]);
        let payload = self
            .client
            .request("/eapi/v1/order", params, Method::Delete)
            .map_err(|error| error.to_string())?;
        Ok(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            venue_order_id: payload
                .get("orderId")
                .map(value_string)
                .or_else(|| Some(venue_order_id.into())),
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos,
            reason: String::new(),
        })
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
        venue_order_id: payload.get("orderId").map(value_string),
        filled_quantity: None,
        occurred_at_unix_nanos: 0,
        reason: payload
            .get("msg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}
fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    request
        .market_id
        .as_deref()
        .or_else(|| request.instrument_id.strip_prefix("instrument:binance:"))
        .or_else(|| request.instrument_id.strip_prefix("instrument:"))
        .map(|value| {
            value
                .rsplit(':')
                .next()
                .unwrap_or(value)
                .to_ascii_uppercase()
        })
        .ok_or_else(|| "Binance Options order requires a symbol".into())
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
