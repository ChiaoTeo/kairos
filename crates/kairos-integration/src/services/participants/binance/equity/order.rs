//! Binance Stocks Trading order-entry connection.

use serde_json::Value;
use std::collections::BTreeMap;

use crate::application::capabilities::{
    DecimalValue, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType,
};
use crate::application::{CommandOutcome, IntegrationError, OrderEntryConnection};
use crate::services::transport::http::command_error_outcome;

use super::client::BinanceEquityRestClient;

pub struct BinanceEquityOrderConnection {
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
        Ok(Self { client })
    }
}

impl OrderEntryConnection for BinanceEquityOrderConnection {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        if request.order_type != OrderType::Limit {
            return Err(IntegrationError::InvalidRequest(
                "Binance Equity order requires a limit order".into(),
            ));
        }
        let price = request.limit_price.ok_or_else(|| {
            IntegrationError::InvalidRequest("Binance Equity limit price is required".into())
        })?;
        let parameters = params(request, price).map_err(IntegrationError::InvalidRequest)?;
        let payload = match self.client.place_order(&parameters) {
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
        if remote_order_id.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "exchange order id is required".into(),
            ));
        }
        let parameters = BTreeMap::from([("orderId", remote_order_id.to_owned())])
            .into_iter()
            .collect::<Vec<_>>();
        let payload = match self.client.cancel_order(&parameters) {
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
            reason: payload
                .get("msg")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .into(),
        }))
    }
}

fn params(
    request: &OrderEntryRequest,
    price: DecimalValue,
) -> Result<Vec<(&'static str, String)>, String> {
    Ok(vec![
        ("symbol", symbol(request)?),
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
        ("clientOrderId", request.order_id.to_string()),
    ])
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
        remote_order_id: payload
            .get("orderId")
            .map(value_string)
            .and_then(|value| kairos_domain_types::RemoteOrderId::new(value).ok()),
        filled_quantity: None,
        occurred_at_unix_nanos: now_nanos().into(),
        reason: payload
            .get("msg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })
}

fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    let value = request.market_id.as_deref().ok_or_else(|| {
        "Binance Equity order requires a market_id resolved by Reference".to_string()
    })?;
    let value = value.rsplit(':').next().unwrap_or(value).trim();
    if value.is_empty() {
        return Err("Binance Equity order symbol is empty".into());
    }
    Ok(value.to_ascii_uppercase())
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
