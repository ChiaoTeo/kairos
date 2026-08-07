//! Binance Equity remote order-query connection.

use serde_json::Value;

use crate::application::{
    Connection, ConnectionSpec, ExternalOrder, ExternalOrderQuery, OrderQueryConnection,
};
use crate::domain::{
    AccessScope, DecimalValue, IntegrationCapability, OrderSide, OrderType, ProductFamily,
    TransportKind,
};
use crate::services::connections::ManagedConnection;

use super::client::BinanceEquityRestClient;

pub struct BinanceEquityOrderQueryConnection {
    connection: ManagedConnection,
    client: BinanceEquityRestClient,
}

impl BinanceEquityOrderQueryConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceEquityRestClient::with_base_url(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let connection = ManagedConnection::new(
            ConnectionSpec {
                connection_id: "execution.binance.equity.order-read.rest".into(),
                provider: "binance".into(),
                product: Some(ProductFamily::Equity),
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::OrderRead,
                credential_id: Some("binance".into()),
                asset_type: Some(crate::domain::AssetType::Equity),
            },
            Vec::new(),
        )?;
        Ok(Self { connection, client })
    }
}

impl Connection for BinanceEquityOrderQueryConnection {
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

impl OrderQueryConnection for BinanceEquityOrderQueryConnection {
    fn open_orders(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String> {
        self.start()?;
        let payload = self
            .client
            .open_orders(&params(query, false))
            .map_err(|e| e.to_string())?;
        normalize_many(&payload)
    }

    fn order_history(&mut self, query: &ExternalOrderQuery) -> Result<Vec<ExternalOrder>, String> {
        self.start()?;
        let payload = self
            .client
            .order_history(&params(query, true))
            .map_err(|e| e.to_string())?;
        normalize_many(&payload)
    }

    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, String> {
        self.start()?;
        let order_id = query
            .order_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| "order_id is required".to_string())?;
        let payload = self
            .client
            .order_detail(&[("orderId", order_id.to_owned())])
            .map_err(|e| e.to_string())?;
        if payload.is_null() {
            return Ok(None);
        }
        normalize_one(&payload).map(Some)
    }
}

fn params(query: &ExternalOrderQuery, history: bool) -> Vec<(&'static str, String)> {
    let mut result = Vec::new();
    if let Some(symbol) = &query.symbol {
        result.push(("symbol", symbol.to_ascii_uppercase()));
    }
    if history {
        if let Some(since) = query.since_unix_millis {
            result.push(("startTime", since.to_string()));
        }
        if let Some(limit) = query.limit {
            result.push(("limit", limit.to_string()));
        }
    }
    result
}

fn normalize_many(payload: &Value) -> Result<Vec<ExternalOrder>, String> {
    let rows = payload
        .as_array()
        .ok_or_else(|| "Binance Equity order query returned a non-array payload".to_string())?;
    rows.iter().map(normalize_one).collect()
}

fn normalize_one(row: &Value) -> Result<ExternalOrder, String> {
    let object = row
        .as_object()
        .ok_or_else(|| "Binance Equity order row is not an object".to_string())?;
    let text = |key: &str| object.get(key).map(value_string).filter(|v| !v.is_empty());
    let order_id =
        text("orderId").ok_or_else(|| "Binance Equity order is missing orderId".to_string())?;
    let symbol = text("symbol").unwrap_or_else(|| "UNKNOWN".into());
    let quantity = decimal(
        object
            .get("origQty")
            .or_else(|| object.get("quantity"))
            .unwrap_or(&Value::Null),
    )?;
    let filled = decimal(
        object
            .get("executedQty")
            .or_else(|| object.get("filledQty"))
            .unwrap_or(&Value::Null),
    )?;
    let average = object
        .get("avgPrice")
        .or_else(|| object.get("price"))
        .filter(|v| !v.is_null())
        .map(decimal)
        .transpose()?;
    Ok(ExternalOrder {
        order_id,
        client_order_id: text("clientOrderId"),
        symbol,
        side: match text("side").as_deref() {
            Some("SELL") => OrderSide::Sell,
            _ => OrderSide::Buy,
        },
        order_type: match text("orderType").as_deref() {
            Some("MARKET") => OrderType::Market,
            _ => OrderType::Limit,
        },
        status: text("status").unwrap_or_else(|| "UNKNOWN".into()),
        quantity,
        filled_quantity: filled,
        average_fill_price: average,
        occurred_at_unix_millis: object
            .get("updateTime")
            .or_else(|| object.get("time"))
            .and_then(Value::as_u64),
    })
}

fn decimal(value: &Value) -> Result<DecimalValue, String> {
    let text = value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string());
    if text == "null" || text.is_empty() {
        return Ok(DecimalValue::new(0, 0));
    }
    let negative = text.starts_with('-');
    let unsigned = text.trim_start_matches('-');
    let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
    let digits = format!("{whole}{fraction}");
    let mantissa = digits
        .parse::<i64>()
        .map_err(|_| format!("invalid decimal: {text}"))?;
    Ok(DecimalValue::new(
        if negative { -mantissa } else { mantissa },
        fraction.len() as u8,
    ))
}

fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}
