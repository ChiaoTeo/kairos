//! OKX authenticated private account WebSocket connection.

use std::net::TcpStream;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::domain::account::{
    ExternalAccountEvent as AccountEvent, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue, ExternalFillEvent as FillEvent,
    ExternalOrderEvent as OrderEvent, ExternalOrderStatus as OrderStatus,
};
use chrono::Utc;
use serde_json::Value;
use tungstenite::{connect, Message, WebSocket};

use crate::application::{
    AccountEventStreamConnection, Connection, ConnectionSpec, IntegrationError,
};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::auth::okx_signature;
use crate::services::connections::ManagedConnection;

type Socket = WebSocket<tungstenite::stream::MaybeTlsStream<TcpStream>>;

pub struct OkxAccountStreamConnection {
    connection: ManagedConnection,
    api_key: String,
    secret: String,
    passphrase: String,
    product: ProductFamily,
    websocket_endpoint: String,
    segment_key: String,
    socket: Option<Socket>,
}

impl OkxAccountStreamConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        websocket_endpoint: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, String> {
        if !matches!(
            product,
            ProductFamily::Spot
                | ProductFamily::CrossMargin
                | ProductFamily::IsolatedMargin
                | ProductFamily::UsdMFutures
                | ProductFamily::CoinMFutures
                | ProductFamily::Options
        ) {
            return Err("OKX account stream requires spot, futures, or options product".into());
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let passphrase = passphrase.into();
        if api_key.trim().is_empty() || secret.trim().is_empty() || passphrase.trim().is_empty() {
            return Err("OKX account stream credentials are required".into());
        }
        let websocket_endpoint = websocket_endpoint.into().trim_end_matches('/').to_string();
        if !(websocket_endpoint.starts_with("wss://") || websocket_endpoint.starts_with("ws://")) {
            return Err("OKX account stream endpoint must start with ws:// or wss://".into());
        }
        let segment_key = segment_key.into();
        if segment_key.trim().is_empty() {
            return Err("OKX account stream segment key is required".into());
        }
        let spec = ConnectionSpec {
            connection_id: format!("account.okx.{}.private-stream", product_name(product)),
            provider: "okx".into(),
            product: Some(product),
            access: AccessScope::Private,
            transport: TransportKind::UserStream,
            capability: IntegrationCapability::AccountStream,
            credential_id: Some("okx".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            api_key,
            secret,
            passphrase,
            product,
            websocket_endpoint,
            segment_key,
            socket: None,
        })
    }

    fn open(&mut self) -> Result<(), String> {
        let (mut socket, _) =
            connect(&self.websocket_endpoint).map_err(|error| error.to_string())?;
        let timestamp = Utc::now().timestamp().to_string();
        let sign = okx_signature(&self.secret, &timestamp, "GET", "/users/self/verify", "")
            .map_err(|error| error.to_string())?;
        socket
            .send(Message::Text(
                serde_json::json!({
                    "op": "login",
                    "args": [{
                        "apiKey": self.api_key,
                        "passphrase": self.passphrase,
                        "timestamp": timestamp,
                        "sign": sign
                    }]
                })
                .to_string()
                .into(),
            ))
            .map_err(|error| error.to_string())?;
        let login = socket.read().map_err(|error| error.to_string())?;
        if !login_succeeded(&login)? {
            return Err(format!("OKX private stream login failed: {login:?}"));
        }
        socket
            .send(Message::Text(
                serde_json::json!({
                    "op": "subscribe",
                    "args": [
                        {"channel": "account"},
                        {"channel": "orders", "instType": instrument_type(self.product)}
                    ]
                })
                .to_string()
                .into(),
            ))
            .map_err(|error| error.to_string())?;
        self.socket = Some(socket);
        Ok(())
    }
}

impl Connection for OkxAccountStreamConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        if self.connection.state().lifecycle == crate::domain::ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.connection.start()?;
        if let Err(error) = self.open() {
            let _ = self.connection.stop();
            return Err(error);
        }
        Ok(())
    }
    fn stop(&mut self) -> Result<(), String> {
        if let Some(mut socket) = self.socket.take() {
            let _ = socket.close(None);
        }
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        let _ = self.stop();
        self.connection.reconnect()?;
        self.open()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}

impl AccountEventStreamConnection for OkxAccountStreamConnection {
    fn next_account_event(&mut self) -> Result<Option<AccountEvent>, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let socket = self.socket.as_mut().ok_or(IntegrationError::NotReady)?;
        let message = socket
            .read()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        match message {
            Message::Text(text) => {
                parse_event(&self.segment_key, &text).map_err(IntegrationError::InvalidPayload)
            }
            Message::Ping(payload) => {
                socket
                    .send(Message::Pong(payload))
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                Ok(None)
            }
            _ => Ok(None),
        }
    }
}

fn login_succeeded(message: &Message) -> Result<bool, String> {
    let Message::Text(text) = message else {
        return Err("OKX private stream did not return a login response".into());
    };
    let value: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    if value.get("event").and_then(Value::as_str) == Some("error") {
        return Ok(false);
    }
    Ok(value.get("event").and_then(Value::as_str) == Some("login")
        && value.get("code").and_then(Value::as_str).unwrap_or("0") == "0")
}

fn parse_event(segment_key: &str, text: &str) -> Result<Option<AccountEvent>, String> {
    let value: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    if value.get("event").is_some() {
        return Ok(None);
    }
    match value
        .get("arg")
        .and_then(|arg| arg.get("channel"))
        .and_then(Value::as_str)
    {
        Some("account") => parse_account_event(segment_key, &value),
        Some("orders") => parse_order_event(segment_key, &value),
        _ => Ok(None),
    }
}

fn parse_account_event(segment_key: &str, value: &Value) -> Result<Option<AccountEvent>, String> {
    let row = value
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX account event data is missing".to_string())?;
    let code = row
        .get("ccy")
        .and_then(Value::as_str)
        .ok_or_else(|| "OKX account event currency is missing".to_string())?;
    let total = decimal_field(row, "eq").or_else(|_| decimal_field(row, "cashBal"))?;
    Ok(Some(AccountEvent::Snapshot(AccountSnapshot {
        segment_key: segment_key.into(),
        balances: vec![Balance {
            asset_id: format!("asset:crypto:{code}"),
            asset_code: code.into(),
            total,
            available: decimal_field(row, "availBal").ok(),
            locked: decimal_field(row, "frozenBal").ok(),
            ..Default::default()
        }],
        collateral: vec![Balance {
            asset_id: format!("asset:crypto:{code}"),
            asset_code: code.into(),
            total,
            available: decimal_field(row, "availBal").ok(),
            locked: decimal_field(row, "frozenBal").ok(),
            ..Default::default()
        }],
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: AccountStatus::Ready,
        observed_at_unix_nanos: row
            .get("uTime")
            .and_then(Value::as_str)
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or_else(now_nanos),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        partial: true,
    })))
}

fn parse_order_event(segment_key: &str, value: &Value) -> Result<Option<AccountEvent>, String> {
    let row = value
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| "OKX order event data is missing".to_string())?;
    let order_id = row
        .get("clOrdId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .or_else(|| row.get("ordId").and_then(Value::as_str))
        .ok_or_else(|| "OKX order event identity is missing".to_string())?;
    let status = match row.get("state").and_then(Value::as_str).unwrap_or_default() {
        "live" | "partially_filled" => {
            if row.get("state").and_then(Value::as_str) == Some("partially_filled") {
                OrderStatus::PartiallyFilled
            } else {
                OrderStatus::Acknowledged
            }
        }
        "filled" => OrderStatus::Filled,
        "canceled" | "mmp_canceled" => OrderStatus::Canceled,
        _ => OrderStatus::Unknown,
    };
    let occurred_at_unix_nanos = row
        .get("uTime")
        .and_then(Value::as_str)
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or_else(now_nanos)
        * 1_000_000;
    let mut events = vec![AccountEvent::Order(OrderEvent {
        order_id: order_id.into(),
        status,
        venue_order_id: row.get("ordId").and_then(Value::as_str).map(str::to_owned),
        filled_quantity: row
            .get("accFillSz")
            .and_then(Value::as_str)
            .map(decimal)
            .transpose()?,
        occurred_at_unix_nanos,
        reason: row
            .get("sMsg")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
    })];
    if let Some(fill_size) = row
        .get("fillSz")
        .and_then(Value::as_str)
        .filter(|value| *value != "0" && !value.is_empty())
    {
        let instrument_id = row
            .get("instId")
            .and_then(Value::as_str)
            .ok_or_else(|| "OKX fill instrument is missing".to_string())?;
        let price = row
            .get("fillPx")
            .and_then(Value::as_str)
            .ok_or_else(|| "OKX fill price is missing".to_string())?;
        let order_id = row
            .get("clOrdId")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .or_else(|| row.get("ordId").and_then(Value::as_str))
            .ok_or_else(|| "OKX fill order identity is missing".to_string())?;
        let fill_id = row
            .get("tradeId")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .unwrap_or_else(|| format!("{order_id}:{occurred_at_unix_nanos}"));
        events.push(AccountEvent::Fill(FillEvent {
            fill_id,
            order_id: order_id.into(),
            segment_key: segment_key.into(),
            instrument_id: format!("instrument:{}", instrument_id.to_ascii_lowercase()),
            side: row
                .get("side")
                .and_then(Value::as_str)
                .unwrap_or("buy")
                .into(),
            quantity: decimal(fill_size)?,
            price: decimal(price)?,
            fee_asset: row.get("feeCcy").and_then(Value::as_str).map(str::to_owned),
            fee_amount: row
                .get("fee")
                .and_then(Value::as_str)
                .filter(|value| *value != "0" && !value.is_empty())
                .map(decimal)
                .transpose()?
                .map(abs_decimal),
            occurred_at_unix_nanos,
        }));
    }
    // The caller supplies the account segment; keep the external fact neutral
    // while retaining the event batch so order state and fills are both seen.
    Ok(Some(AccountEvent::Batch(events)))
}

fn abs_decimal(value: DecimalValue) -> DecimalValue {
    DecimalValue::new(value.mantissa.saturating_abs(), value.scale)
}

fn decimal_field(value: &Value, field: &str) -> Result<DecimalValue, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("OKX field is missing: {field}"))
        .and_then(decimal)
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    let value = value.trim();
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

fn product_name(product: ProductFamily) -> &'static str {
    match product {
        ProductFamily::Spot => "spot",
        ProductFamily::CrossMargin => "cross-margin",
        ProductFamily::IsolatedMargin => "isolated-margin",
        ProductFamily::UsdMFutures => "swap",
        ProductFamily::CoinMFutures => "futures",
        ProductFamily::Options => "options",
        _ => "account",
    }
}

fn instrument_type(product: ProductFamily) -> &'static str {
    match product {
        ProductFamily::Spot => "SPOT",
        ProductFamily::CrossMargin | ProductFamily::IsolatedMargin => "MARGIN",
        ProductFamily::UsdMFutures => "SWAP",
        ProductFamily::CoinMFutures => "FUTURES",
        ProductFamily::Options => "OPTION",
        _ => "ANY",
    }
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::parse_event;
    use crate::domain::account::{
        ExternalAccountEvent as AccountEvent, ExternalOrderStatus as OrderStatus,
    };

    #[test]
    fn parses_okx_order_event_into_neutral_fact() {
        let event = parse_event(
            "spot",
            r#"{"arg":{"channel":"orders"},"data":[{"clOrdId":"local-1","ordId":"88","state":"filled","accFillSz":"1.25","uTime":"1700000000000"}]}"#,
        )
        .unwrap()
        .unwrap();
        let AccountEvent::Batch(events) = event else {
            panic!("expected event batch")
        };
        let AccountEvent::Order(order) = &events[0] else {
            panic!("expected order")
        };
        assert_eq!(order.status, OrderStatus::Filled);
        assert_eq!(order.venue_order_id.as_deref(), Some("88"));
        assert_eq!(order.filled_quantity.unwrap().mantissa, 125);
    }

    #[test]
    fn parses_okx_fill_and_fee_fact() {
        let event = parse_event(
            "spot",
            r#"{"arg":{"channel":"orders"},"data":[{"clOrdId":"local-1","ordId":"88","tradeId":"99","instId":"BTC-USDT","side":"buy","state":"partially_filled","accFillSz":"1.25","fillSz":"0.25","fillPx":"100.5","fee":"-0.01","feeCcy":"USDT","uTime":"1700000000000"}]}"#,
        )
        .unwrap()
        .unwrap();
        let AccountEvent::Batch(events) = event else {
            panic!("expected event batch")
        };
        let AccountEvent::Fill(fill) = &events[1] else {
            panic!("expected fill event")
        };
        assert_eq!(fill.fill_id, "99");
        assert_eq!(fill.segment_key, "spot");
        assert_eq!(fill.quantity.mantissa, 25);
        assert_eq!(fill.fee_amount.unwrap().mantissa, 1);
    }

    #[test]
    fn parses_okx_account_event_into_snapshot_fact() {
        let event = parse_event(
            "spot",
            r#"{"arg":{"channel":"account"},"data":[{"ccy":"USDT","eq":"10.5","availBal":"9.5","frozenBal":"1","uTime":"1700000000000"}]}"#,
        )
        .unwrap()
        .unwrap();
        let AccountEvent::Snapshot(snapshot) = event else {
            panic!("expected snapshot")
        };
        assert_eq!(snapshot.segment_key, "spot");
        assert_eq!(snapshot.balances[0].total.mantissa, 105);
    }
}
