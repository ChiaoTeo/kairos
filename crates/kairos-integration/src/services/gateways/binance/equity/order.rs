//! Binance Stocks Trading order-entry connection.

use serde_json::Value;
use std::collections::BTreeMap;

use crate::application::{Connection, ConnectionSpec, OrderEntryConnection};
use crate::domain::{
    AccessScope, DecimalValue, IntegrationCapability, OrderEntryEvent, OrderEntryRequest,
    OrderEntryStatus, OrderSide, OrderType, ProductFamily, TransportKind,
};
use crate::services::connections::ManagedConnection;

use super::client::BinanceEquityRestClient;

pub struct BinanceEquityOrderConnection {
    connection: ManagedConnection,
    client: BinanceEquityRestClient,
}

impl BinanceEquityOrderConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceEquityRestClient::with_base_url(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let connection = ManagedConnection::new(
            ConnectionSpec {
                connection_id: "execution.binance.equity.rest".into(),
                provider: "binance".into(),
                product: Some(ProductFamily::Equity),
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::OrderEntry,
                credential_id: Some("binance".into()),
                asset_type: Some(crate::domain::AssetType::Equity),
            },
            Vec::new(),
        )?;
        Ok(Self { connection, client })
    }
}

impl Connection for BinanceEquityOrderConnection {
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

impl OrderEntryConnection for BinanceEquityOrderConnection {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        self.start()?;
        if request.order_type != OrderType::Limit {
            return Err("Binance Equity order requires a limit order".into());
        }
        let price = request
            .limit_price
            .ok_or_else(|| "Binance Equity limit price is required".to_string())?;
        let payload = self
            .client
            .place_order(&params(request, price))
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
        if venue_order_id.trim().is_empty() {
            return Err("venue order id is required".into());
        }
        let payload = self
            .client
            .cancel_order(
                &BTreeMap::from([("orderId", venue_order_id.to_owned())])
                    .into_iter()
                    .collect::<Vec<_>>(),
            )
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
            reason: payload
                .get("msg")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .into(),
        })
    }
}

fn params(request: &OrderEntryRequest, price: DecimalValue) -> Vec<(&'static str, String)> {
    vec![
        ("symbol", symbol(request)),
        (
            "side",
            match request.side {
                OrderSide::Buy => "BUY",
                OrderSide::Sell => "SELL",
            }
            .into(),
        ),
        ("orderType", "LIMIT".into()),
        ("quoteAsset", "USDC".into()),
        ("price", decimal(price)),
        ("quantity", decimal(request.quantity)),
        ("timeInForce", "DAY".into()),
        ("tradingSession", "RTH".into()),
        ("walletType", "MAIN".into()),
        ("tokenize", "true".into()),
        ("clientOrderId", request.order_id.clone()),
    ]
}

fn normalize(request: &OrderEntryRequest, payload: &Value) -> Result<OrderEntryEvent, String> {
    let status = match payload
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or_default()
    {
        "NEW" | "PENDING_NEW" | "ACCEPTED" => OrderEntryStatus::Accepted,
        "PARTIALLY_FILLED" => OrderEntryStatus::PartiallyFilled,
        "FILLED" => OrderEntryStatus::Filled,
        "CANCELED" => OrderEntryStatus::Canceled,
        "REJECTED" => OrderEntryStatus::Rejected,
        _ => OrderEntryStatus::Unknown,
    };
    Ok(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status,
        venue_order_id: payload.get("orderId").map(value_string),
        filled_quantity: None,
        occurred_at_unix_nanos: now_nanos(),
        reason: payload
            .get("msg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}

fn symbol(request: &OrderEntryRequest) -> String {
    request
        .market_id
        .clone()
        .or_else(|| {
            request
                .instrument_id
                .strip_prefix("instrument:equity:")
                .map(str::to_owned)
        })
        .unwrap_or_else(|| request.instrument_id.clone())
        .to_ascii_uppercase()
}
fn decimal(value: DecimalValue) -> String {
    format_decimal(value)
}
fn format_decimal(value: DecimalValue) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.unsigned_abs().to_string();
    let scale = value.scale as usize;
    let padded = format!("{digits:0>width$}", width = scale + 1);
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}
fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}
fn now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
