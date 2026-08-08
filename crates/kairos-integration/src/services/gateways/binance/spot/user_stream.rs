//! Binance Spot private user stream.

use std::net::TcpStream;

use crate::domain::account::{
    ExternalAccountEvent as AccountEvent, ExternalAccountSnapshot as AccountSnapshot,
    ExternalAccountStatus as AccountStatus, ExternalBalance as Balance,
    ExternalDecimal as DecimalValue, ExternalFillEvent as FillEvent,
    ExternalOrderEvent as OrderEvent, ExternalOrderStatus as OrderStatus,
};
use serde_json::Value;
use tungstenite::{connect, Message, WebSocket};

use crate::application::{
    AccountEventStreamConnection, Connection, ConnectionSpec, IntegrationError,
};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::connections::ManagedConnection;

use super::account::BinanceSpotAccountClient;

type Socket = WebSocket<tungstenite::stream::MaybeTlsStream<TcpStream>>;

pub struct BinanceSpotAccountStreamConnection {
    connection: ManagedConnection,
    client: BinanceSpotAccountClient,
    product: ProductFamily,
    websocket_endpoint: String,
    socket: Option<Socket>,
    segment_key: String,
}

impl BinanceSpotAccountStreamConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
        websocket_endpoint: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, String> {
        Self::new_for_product(
            ProductFamily::Spot,
            api_key,
            secret,
            base_url,
            websocket_endpoint,
            segment_key,
        )
    }

    pub fn new_for_product(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
        websocket_endpoint: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let websocket_endpoint = websocket_endpoint.into().trim_end_matches('/').to_string();
        if !(websocket_endpoint.starts_with("wss://") || websocket_endpoint.starts_with("ws://")) {
            return Err("Binance user stream endpoint must start with ws:// or wss://".into());
        }
        let segment_key = segment_key.into();
        if segment_key.trim().is_empty() {
            return Err("Binance user stream segment key is required".into());
        }
        let spec = ConnectionSpec {
            connection_id: format!("account.binance.{product:?}.user-stream").to_ascii_lowercase(),
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            access: AccessScope::Private,
            transport: TransportKind::UserStream,
            capability: IntegrationCapability::AccountStream,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
            product,
            websocket_endpoint,
            socket: None,
            segment_key,
        })
    }

    fn open(&mut self) -> Result<(), String> {
        let listen_key = match self.product {
            ProductFamily::Spot => self.client.listen_key(),
            ProductFamily::CrossMargin => self.client.margin_listen_key(None),
            ProductFamily::IsolatedMargin => {
                let symbol = self
                    .segment_key
                    .split_once(':')
                    .map(|(_, symbol)| symbol)
                    .filter(|symbol| !symbol.trim().is_empty())
                    .ok_or_else(|| {
                        "Binance isolated margin user stream requires segment key isolated_margin:<symbol>"
                            .to_string()
                    })?;
                self.client.margin_listen_key(Some(symbol))
            }
            _ => return Err(format!("unsupported Binance account stream product: {:?}", self.product)),
        }
        .map_err(|error| error.to_string())?;
        let endpoint = format!("{}/ws/{listen_key}", self.websocket_endpoint);
        let (socket, _) = connect(endpoint).map_err(|error| error.to_string())?;
        self.socket = Some(socket);
        Ok(())
    }
}

impl Connection for BinanceSpotAccountStreamConnection {
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

impl AccountEventStreamConnection for BinanceSpotAccountStreamConnection {
    fn next_account_event(&mut self) -> Result<Option<AccountEvent>, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let message = self
            .socket
            .as_mut()
            .ok_or(IntegrationError::NotReady)?
            .read()
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let Message::Text(text) = message else {
            return Ok(None);
        };
        parse_user_event(&self.segment_key, &text).map_err(IntegrationError::InvalidPayload)
    }
}

fn parse_user_event(segment_key: &str, text: &str) -> Result<Option<AccountEvent>, String> {
    let value: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    match value.get("e").and_then(Value::as_str).unwrap_or_default() {
        "executionReport" => {
            let local_order_id = value
                .get("c")
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance execution report client order id is missing".to_string())?;
            let status = match value.get("X").and_then(Value::as_str).unwrap_or_default() {
                "NEW" => OrderStatus::Acknowledged,
                "PARTIALLY_FILLED" => OrderStatus::PartiallyFilled,
                "FILLED" => OrderStatus::Filled,
                "CANCELED" => OrderStatus::Canceled,
                "REJECTED" => OrderStatus::Rejected,
                "EXPIRED" => OrderStatus::Expired,
                _ => OrderStatus::Unknown,
            };
            let event = OrderEvent {
                order_id: local_order_id.to_owned(),
                status,
                venue_order_id: value.get("i").map(value_as_string),
                filled_quantity: value
                    .get("z")
                    .and_then(Value::as_str)
                    .map(decimal)
                    .transpose()?,
                occurred_at_unix_nanos: value.get("E").and_then(Value::as_u64).unwrap_or_default()
                    * 1_000_000,
                reason: value
                    .get("r")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .into(),
            };
            let mut events = vec![AccountEvent::Order(event)];
            if let Some(quantity) = value
                .get("l")
                .and_then(Value::as_str)
                .filter(|value| *value != "0" && !value.is_empty())
            {
                let symbol = value
                    .get("s")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "Binance execution report symbol is missing".to_string())?;
                let price = value
                    .get("L")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "Binance execution report fill price is missing".to_string())?;
                let occurred_at_unix_nanos =
                    value.get("E").and_then(Value::as_u64).unwrap_or_default() * 1_000_000;
                let fill_id = value
                    .get("t")
                    .map(value_as_string)
                    .filter(|value| value != "-1")
                    .unwrap_or_else(|| format!("{local_order_id}:{occurred_at_unix_nanos}"));
                events.push(AccountEvent::Fill(FillEvent {
                    fill_id,
                    order_id: local_order_id.to_owned(),
                    segment_key: segment_key.into(),
                    instrument_id: format!("instrument:{}", symbol.to_ascii_lowercase()),
                    side: value
                        .get("S")
                        .and_then(Value::as_str)
                        .unwrap_or("BUY")
                        .into(),
                    quantity: decimal(quantity)?,
                    price: decimal(price)?,
                    fee_asset: value.get("N").and_then(Value::as_str).map(str::to_owned),
                    fee_amount: value
                        .get("n")
                        .and_then(Value::as_str)
                        .filter(|value| *value != "0" && !value.is_empty())
                        .map(decimal)
                        .transpose()?,
                    occurred_at_unix_nanos,
                }));
            }
            Ok(Some(AccountEvent::Batch(events)))
        }
        "outboundAccountPosition" | "balanceUpdate" => {
            let balances = if value.get("B").is_some() {
                value
                    .get("B")
                    .and_then(Value::as_array)
                    .map_or(&[][..], Vec::as_slice)
                    .iter()
                    .map(normalize_balance)
                    .collect::<Result<Vec<_>, _>>()?
            } else {
                vec![Balance {
                    asset_id: format!(
                        "asset:crypto:{}",
                        value.get("a").and_then(Value::as_str).unwrap_or_default()
                    ),
                    asset_code: value
                        .get("a")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .into(),
                    total: decimal(value.get("d").and_then(Value::as_str).unwrap_or("0"))?,
                    ..Default::default()
                }]
            };
            Ok(Some(AccountEvent::Snapshot(AccountSnapshot {
                segment_key: segment_key.into(),
                balances,
                collateral: Vec::new(),
                positions: Vec::new(),
                open_orders: Vec::new(),
                status: AccountStatus::Ready,
                observed_at_unix_nanos: value.get("E").and_then(Value::as_u64).unwrap_or_default()
                    * 1_000_000,
                equity: None,
                initial_equity: None,
                net_profit: None,
                account_model: None,
                margin_mode: None,
                position_mode: None,
                partial: true,
            })))
        }
        _ => Ok(None),
    }
}

fn normalize_balance(value: &Value) -> Result<Balance, String> {
    let code = value
        .get("a")
        .and_then(Value::as_str)
        .ok_or_else(|| "Binance balance asset is missing".to_string())?;
    let free = decimal(value.get("f").and_then(Value::as_str).unwrap_or("0"))?;
    let locked = decimal(value.get("l").and_then(Value::as_str).unwrap_or("0"))?;
    let scale = free.scale.max(locked.scale);
    Ok(Balance {
        asset_id: format!("asset:crypto:{code}"),
        asset_code: code.into(),
        total: DecimalValue::new(rescale(free, scale)? + rescale(locked, scale)?, scale),
        available: Some(free),
        locked: Some(locked),
        ..Default::default()
    })
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let mut parts = unsigned.split('.');
    let whole = parts.next().unwrap_or("0");
    let fraction = parts.next().unwrap_or("");
    if parts.next().is_some()
        || !whole.chars().all(|c| c.is_ascii_digit())
        || !fraction.chars().all(|c| c.is_ascii_digit())
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

fn rescale(value: DecimalValue, scale: u8) -> Result<i64, String> {
    value
        .mantissa
        .checked_mul(10_i64.pow((scale - value.scale) as u32))
        .ok_or_else(|| "decimal rescale overflow".into())
}

fn value_as_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}

#[cfg(test)]
mod tests {
    use super::parse_user_event;
    use crate::domain::account::{
        ExternalAccountEvent as AccountEvent, ExternalOrderStatus as OrderStatus,
    };

    #[test]
    fn parses_execution_report_into_account_order_fact() {
        let event = parse_user_event(
            "spot",
            r#"{"e":"executionReport","E":1000,"c":"order-1","i":42,"X":"FILLED","z":"0.25","r":"NONE"}"#,
        )
        .unwrap()
        .unwrap();
        let AccountEvent::Batch(events) = event else {
            panic!("expected account event batch")
        };
        let AccountEvent::Order(event) = &events[0] else {
            panic!("expected order event")
        };
        assert_eq!(event.order_id, "order-1");
        assert_eq!(event.status, OrderStatus::Filled);
        assert_eq!(event.venue_order_id.as_deref(), Some("42"));
        assert_eq!(event.filled_quantity.unwrap().mantissa, 25);
    }

    #[test]
    fn parses_execution_report_fill_and_fee_fact() {
        let event = parse_user_event(
            "spot",
            r#"{"e":"executionReport","E":1000,"c":"order-1","i":42,"t":7,"s":"BTCUSDT","S":"BUY","X":"PARTIALLY_FILLED","z":"0.25","l":"0.10","L":"100.5","n":"0.01","N":"USDT","r":"NONE"}"#,
        )
        .unwrap()
        .unwrap();
        let AccountEvent::Batch(events) = event else {
            panic!("expected event batch")
        };
        let AccountEvent::Fill(fill) = &events[1] else {
            panic!("expected fill event")
        };
        assert_eq!(fill.fill_id, "7");
        assert_eq!(fill.quantity.mantissa, 10);
        assert_eq!(fill.price.mantissa, 1005);
        assert_eq!(fill.fee_asset.as_deref(), Some("USDT"));
        assert_eq!(fill.fee_amount.unwrap().mantissa, 1);
    }

    #[test]
    fn parses_account_position_balance_into_snapshot_fact() {
        let event = parse_user_event(
            "spot",
            r#"{"e":"outboundAccountPosition","E":1000,"B":[{"a":"USDT","f":"10.25","l":"0.75"}]}"#,
        )
        .unwrap()
        .unwrap();
        let AccountEvent::Snapshot(snapshot) = event else {
            panic!("expected snapshot event")
        };
        assert_eq!(snapshot.segment_key, "spot");
        assert_eq!(snapshot.balances[0].total.mantissa, 1100);
    }
}
