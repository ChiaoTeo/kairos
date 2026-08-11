//! Binance Spot private user stream.

use crate::application::capabilities::account_facts::{
    canonical_account_identity, ExternalAccountEvent as AccountEvent,
    ExternalAccountSnapshot as AccountSnapshot, ExternalAccountStatus as AccountStatus,
    ExternalBalance as Balance, ExternalDecimal as DecimalValue, ExternalFillEvent as FillEvent,
    ExternalOrderEvent as OrderEvent, ExternalOrderStatus as OrderStatus,
};
use crate::application::{AccountEventReceive, AccountEventStreamConnection, IntegrationError};
use crate::services::participants::binance::ConnectionDomain;
use crate::services::transport::websocket::{SocketEvent, TokioSocket};
use serde_json::Value;

use super::account::BinanceSpotAccountClient;

pub struct BinanceSpotAccountStreamConnection {
    state: crate::domain::ConnectionState,
    client: BinanceSpotAccountClient,
    product: ConnectionDomain,
    websocket_endpoint: String,
    socket: Option<TokioSocket>,
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
            ConnectionDomain::Spot,
            api_key,
            secret,
            base_url,
            websocket_endpoint,
            segment_key,
        )
    }

    pub fn new_for_product(
        product: ConnectionDomain,
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
        Ok(Self {
            state: crate::domain::ConnectionState::new(super::super::descriptor(
                format!("account.binance.{product:?}.user-stream").to_ascii_lowercase(),
                product.as_str(),
            )?),
            client,
            product,
            websocket_endpoint,
            socket: None,
            segment_key,
        })
    }

    fn open(&mut self) -> Result<(), String> {
        let listen_key = match self.product {
            ConnectionDomain::Spot => self.client.listen_key(),
            ConnectionDomain::CrossMargin => self.client.margin_listen_key(None),
            ConnectionDomain::IsolatedMargin => {
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
        self.socket = Some(TokioSocket::connect(endpoint)?);
        Ok(())
    }
}

impl AccountEventStreamConnection for BinanceSpotAccountStreamConnection {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.state.lifecycle == crate::domain::ConnectionLifecycle::Ready {
            return Ok(());
        }
        if let Err(error) = self.open() {
            self.state.mark_failed(error.clone());
            return Err(IntegrationError::Transport(error));
        }
        self.state.mark_ready(true);
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.socket.take();
        self.state.mark_stopped();
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.socket.take();
        match self.open() {
            Ok(()) => {
                self.state.mark_reconnected(true);
                Ok(())
            }
            Err(error) => {
                self.state.mark_failed(error.clone());
                Err(IntegrationError::Transport(error))
            }
        }
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.state.health()
    }

    fn recv_account_event(
        &mut self,
        timeout: std::time::Duration,
    ) -> Result<AccountEventReceive, IntegrationError> {
        self.connect_channel()?;
        let event = self
            .socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .recv_timeout(timeout)
            .map_err(IntegrationError::Transport)?;
        let Some(event) = event else {
            return Ok(AccountEventReceive::Idle);
        };
        let text = match event {
            SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Text(text)) => text,
            SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Ping(payload)) => {
                self.socket
                    .as_ref()
                    .ok_or(IntegrationError::NotReady)?
                    .send_pong(payload.to_vec())
                    .map_err(IntegrationError::Transport)?;
                return Ok(AccountEventReceive::Idle);
            }
            SocketEvent::Message(_) => return Ok(AccountEventReceive::Idle),
            SocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
            SocketEvent::Backpressure => {
                return Err(IntegrationError::Backpressure(
                    "Binance Spot account event queue overflowed".into(),
                ))
            }
        };
        let product = match self.product {
            ConnectionDomain::Spot
            | ConnectionDomain::CrossMargin
            | ConnectionDomain::IsolatedMargin => "binance-spot",
            _ => "binance-spot",
        };
        Ok(parse_user_event(&self.segment_key, product, &text)
            .map_err(IntegrationError::InvalidPayload)?
            .map(AccountEventReceive::Event)
            .unwrap_or(AccountEventReceive::Idle))
    }
}

pub(crate) fn parse_user_event(
    segment_key: &str,
    product: &str,
    text: &str,
) -> Result<Option<AccountEvent>, String> {
    let outer: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    let value = outer.get("event").unwrap_or(&outer);
    parse_user_event_value(segment_key, product, value)
}

pub(crate) fn parse_user_event_value(
    segment_key: &str,
    product: &str,
    value: &Value,
) -> Result<Option<AccountEvent>, String> {
    match value.get("e").and_then(Value::as_str).unwrap_or_default() {
        "eventStreamTerminated" => Err(
            "Binance Spot user data subscription terminated; resynchronization is required".into(),
        ),
        "serverShutdown" => Err("Binance Spot WebSocket API server is shutting down".into()),
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
                order_id: kairos_domain_types::OrderId::new(local_order_id)?,
                status,
                remote_order_id: value
                    .get("i")
                    .map(value_as_string)
                    .map(kairos_domain_types::RemoteOrderId::new)
                    .transpose()?,
                filled_quantity: value
                    .get("z")
                    .and_then(Value::as_str)
                    .map(decimal)
                    .transpose()?,
                occurred_at_unix_nanos: (value
                    .get("E")
                    .and_then(Value::as_u64)
                    .unwrap_or_default()
                    * 1_000_000)
                    .into(),
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
                let (instrument_id, _) = canonical_account_identity(product, symbol)?;
                let occurred_at_unix_nanos =
                    value.get("E").and_then(Value::as_u64).unwrap_or_default() * 1_000_000;
                let fill_id = value
                    .get("t")
                    .map(value_as_string)
                    .filter(|value| value != "-1")
                    .unwrap_or_else(|| format!("{local_order_id}:{occurred_at_unix_nanos}"));
                events.push(AccountEvent::Fill(FillEvent {
                    fill_id: kairos_domain_types::FillId::new(fill_id)?,
                    order_id: kairos_domain_types::OrderId::new(local_order_id)?,
                    segment_key: kairos_domain_types::SegmentKey::new(segment_key)?,
                    instrument_id,
                    side: value
                        .get("S")
                        .and_then(Value::as_str)
                        .unwrap_or("BUY")
                        .into(),
                    quantity: decimal(quantity)?,
                    price: decimal(price)?,
                    fee_asset: value
                        .get("N")
                        .and_then(Value::as_str)
                        .map(kairos_domain_types::Currency::new)
                        .transpose()?,
                    fee_amount: value
                        .get("n")
                        .and_then(Value::as_str)
                        .filter(|value| *value != "0" && !value.is_empty())
                        .map(decimal)
                        .transpose()?,
                    occurred_at_unix_nanos: occurred_at_unix_nanos.into(),
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
                    asset_id: kairos_domain_types::AssetId::new(format!(
                        "asset:crypto:{}",
                        value.get("a").and_then(Value::as_str).unwrap_or_default()
                    ))?,
                    asset_code: kairos_domain_types::Currency::new(
                        value.get("a").and_then(Value::as_str).unwrap_or_default(),
                    )?,
                    total: decimal(value.get("d").and_then(Value::as_str).unwrap_or("0"))?,
                    ..Default::default()
                }]
            };
            Ok(Some(AccountEvent::Snapshot(AccountSnapshot {
                segment_key: kairos_domain_types::SegmentKey::new(segment_key)?,
                balances,
                collateral: Vec::new(),
                positions: Vec::new(),
                open_orders: Vec::new(),
                status: AccountStatus::Ready,
                observed_at_unix_nanos: (value
                    .get("E")
                    .and_then(Value::as_u64)
                    .unwrap_or_default()
                    * 1_000_000)
                    .into(),
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
        asset_id: kairos_domain_types::AssetId::new(format!("asset:crypto:{code}"))?,
        asset_code: kairos_domain_types::Currency::new(code)?,
        total: DecimalValue::new(rescale(free, scale)? + rescale(locked, scale)?, scale),
        available: Some(free),
        locked: Some(locked),
        ..Default::default()
    })
}

fn decimal(value: &str) -> Result<DecimalValue, String> {
    DecimalValue::parse(value)
}

fn rescale(value: DecimalValue, scale: u8) -> Result<i64, String> {
    value.rescale_exact(scale).map(|value| value.mantissa)
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
    use crate::application::capabilities::account_facts::{
        ExternalAccountEvent as AccountEvent, ExternalOrderStatus as OrderStatus,
    };

    #[test]
    fn parses_execution_report_into_account_order_fact() {
        let event = parse_user_event(
            "spot",
            "binance-spot",
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
        assert_eq!(event.remote_order_id.as_deref(), Some("42"));
        assert_eq!(event.filled_quantity.unwrap().mantissa, 25);
    }

    #[test]
    fn parses_execution_report_fill_and_fee_fact() {
        let event = parse_user_event(
            "spot",
            "binance-spot",
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
            "binance-spot",
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
