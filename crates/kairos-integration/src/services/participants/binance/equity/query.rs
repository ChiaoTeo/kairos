//! Binance Equity remote order-query connection.

use kairos_domain_types::{ClientOrderId, OrderId, Symbol, UnixNanos};
use serde_json::Value;

use crate::application::capabilities::{DecimalValue, OrderSide, OrderType};
use crate::application::{
    ExternalOrder, ExternalOrderQuery, IntegrationError, OrderQueryConnection,
};

use super::client::BinanceEquityRestClient;

pub struct BinanceEquityOrderQueryConnection {
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
        Ok(Self { client })
    }
}

impl OrderQueryConnection for BinanceEquityOrderQueryConnection {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let payload = self
            .client
            .open_orders(&params(query, false))
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_many(&payload).map_err(IntegrationError::InvalidPayload)
    }

    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let payload = self
            .client
            .order_history(&params(query, true))
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_many(&payload).map_err(IntegrationError::InvalidPayload)
    }

    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let order_id = query
            .order_id
            .as_deref()
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| IntegrationError::InvalidRequest("order_id is required".into()))?;
        let payload = self
            .client
            .order_detail(&[("orderId", order_id.to_owned())])
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        if payload.is_null() {
            return Ok(None);
        }
        normalize_one(&payload)
            .map(Some)
            .map_err(IntegrationError::InvalidPayload)
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
        binding_id: String::new(),
        order_id: OrderId::try_from(order_id).map_err(|error| error.to_string())?,
        client_order_id: text("clientOrderId")
            .map(ClientOrderId::try_from)
            .transpose()
            .map_err(|error| error.to_string())?,
        symbol: Symbol::try_from(symbol).map_err(|error| error.to_string())?,
        side: match text("side").as_deref() {
            Some("SELL") => OrderSide::Sell,
            _ => OrderSide::Buy,
        },
        order_type: match text("orderType").as_deref() {
            Some("MARKET") => OrderType::Market,
            _ => OrderType::Limit,
        },
        status: crate::application::capabilities::execution_facts::normalize_order_status(
            &text("status").unwrap_or_else(|| "UNKNOWN".into()),
        ),
        quantity,
        filled_quantity: filled,
        average_fill_price: average,
        occurred_at_unix_millis: object
            .get("updateTime")
            .or_else(|| object.get("time"))
            .and_then(Value::as_u64)
            .map(|value| UnixNanos::from(value.saturating_mul(1_000_000))),
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
    DecimalValue::parse(&text)
}

fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}
