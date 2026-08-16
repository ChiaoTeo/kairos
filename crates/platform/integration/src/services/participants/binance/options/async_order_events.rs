//! Async Binance Options private order/fill channel.

use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::{Currency, FillId, OrderId, OrderStatus, Symbol, UnixNanos};
use serde_json::Value;

use crate::application::capabilities::{
    ConnectionHealth, ConnectionLifecycle, DecimalValue, OrderSide, OrderType,
};
use crate::application::{
    AsyncOrderEventSource, ExternalEventEnvelope, ExternalExecutionEvent, IntegrationError,
};
use crate::services::transport::http::ExchangeError;
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};

use super::account::BinanceOptionsAccountClient;

pub(crate) struct BinanceOptionsAsyncOrderEventSource {
    binding_id: String,
    channel_id: String,
    client: BinanceOptionsAccountClient,
    websocket_endpoint: String,
    event_queue_capacity: usize,
    socket: Option<AsyncTokioSocket>,
    listen_key: Option<String>,
    next_keepalive: Option<tokio::time::Instant>,
    lifecycle: ConnectionLifecycle,
    channel_epoch: u64,
    last_error: Option<String>,
}

impl BinanceOptionsAsyncOrderEventSource {
    pub(crate) fn new(
        binding_id: impl Into<String>,
        client: BinanceOptionsAccountClient,
        websocket_endpoint: impl Into<String>,
        event_queue_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        let binding_id = binding_id.into();
        if binding_id.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Options binding id is required".into(),
            ));
        }
        let websocket_endpoint = websocket_endpoint.into().trim_end_matches('/').to_string();
        if !(websocket_endpoint.starts_with("wss://") || websocket_endpoint.starts_with("ws://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Options private-stream endpoint must start with ws:// or wss://".into(),
            ));
        }
        if event_queue_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Binance Options event queue capacity must be positive".into(),
            ));
        }
        Ok(Self {
            channel_id: format!("{binding_id}.order-events"),
            binding_id,
            client,
            websocket_endpoint,
            event_queue_capacity,
            socket: None,
            listen_key: None,
            next_keepalive: None,
            lifecycle: ConnectionLifecycle::Created,
            channel_epoch: 0,
            last_error: None,
        })
    }

    fn record_failure(&mut self, error: &IntegrationError) {
        self.lifecycle = ConnectionLifecycle::Degraded;
        self.last_error = Some(error.to_string());
    }
}

impl AsyncOrderEventSource for BinanceOptionsAsyncOrderEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some() {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        let listen_key = match tokio::time::timeout(
            std::time::Duration::from_secs(10),
            self.client.listen_key_async(),
        )
        .await
        {
            Ok(Ok(value)) => value,
            Ok(Err(error)) => {
                let error = map_exchange_error(error);
                self.record_failure(&error);
                return Err(error);
            }
            Err(_) => {
                let error = IntegrationError::Unavailable(
                    "timed out creating Binance Options listen key".into(),
                );
                self.record_failure(&error);
                return Err(error);
            }
        };
        let endpoint = format!("{}/{listen_key}", self.websocket_endpoint);
        match tokio::time::timeout(
            std::time::Duration::from_secs(10),
            AsyncTokioSocket::connect(&endpoint, self.event_queue_capacity),
        )
        .await
        {
            Ok(Ok(socket)) => {
                self.socket = Some(socket);
                self.listen_key = Some(listen_key);
                self.next_keepalive =
                    Some(tokio::time::Instant::now() + std::time::Duration::from_secs(30 * 60));
                self.channel_epoch = self.channel_epoch.saturating_add(1);
                self.lifecycle = ConnectionLifecycle::Ready;
                self.last_error = None;
                Ok(())
            }
            Ok(Err(message)) => {
                let error = IntegrationError::Transport(message);
                self.record_failure(&error);
                Err(error)
            }
            Err(_) => {
                let error = IntegrationError::Unavailable(
                    "timed out connecting Binance Options private stream".into(),
                );
                self.record_failure(&error);
                Err(error)
            }
        }
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.lifecycle = ConnectionLifecycle::Stopping;
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.listen_key = None;
        self.next_keepalive = None;
        self.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            authenticated: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            last_error: self.last_error.clone(),
        }
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        self.connect_channel().await?;
        loop {
            let keepalive_at = self.next_keepalive.ok_or(IntegrationError::NotReady)?;
            let event = {
                let socket = self.socket.as_mut().ok_or(IntegrationError::NotReady)?;
                match tokio::time::timeout_at(keepalive_at, socket.next_event()).await {
                    Ok(event) => event,
                    Err(_) => {
                        let listen_key = self
                            .listen_key
                            .as_deref()
                            .ok_or(IntegrationError::NotReady)?;
                        if let Err(error) = self.client.keepalive_listen_key_async(listen_key).await
                        {
                            let error = IntegrationError::ResyncRequired(format!(
                                "Binance Options listen-key keepalive failed: {error}"
                            ));
                            self.record_failure(&error);
                            return Err(error);
                        }
                        self.next_keepalive = Some(
                            tokio::time::Instant::now() + std::time::Duration::from_secs(30 * 60),
                        );
                        continue;
                    }
                }
            };
            let socket = self.socket.as_mut().ok_or(IntegrationError::NotReady)?;
            let text = match event {
                AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Text(text)) => {
                    text.to_string()
                }
                AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Ping(
                    payload,
                )) => {
                    socket
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                    continue;
                }
                AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Close(
                    frame,
                )) => {
                    let error = IntegrationError::ResyncRequired(format!(
                        "Binance Options private stream closed: {frame:?}"
                    ));
                    self.record_failure(&error);
                    return Err(error);
                }
                AsyncSocketEvent::Message(_) => continue,
                AsyncSocketEvent::Error(message) => {
                    let error = IntegrationError::ResyncRequired(message);
                    self.record_failure(&error);
                    return Err(error);
                }
                AsyncSocketEvent::Backpressure => {
                    let error = IntegrationError::Backpressure(
                        "Binance Options order event queue overflowed; reconciliation is required"
                            .into(),
                    );
                    self.record_failure(&error);
                    return Err(error);
                }
            };
            if let Some(event) = parse_order_trade_update(
                &self.binding_id,
                &self.channel_id,
                self.channel_epoch,
                UnixNanos::from(now_unix_nanos()),
                &text,
            )? {
                return Ok(event);
            }
        }
    }
}

fn map_exchange_error(error: ExchangeError) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::LocalRateLimit { message, .. } => IntegrationError::RateLimited(message),
        ExchangeError::Http { status: 401, body } => IntegrationError::Authentication(body),
        ExchangeError::Http { status: 403, body } => IntegrationError::Authorization(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}

pub(super) fn parse_order_trade_update(
    binding_id: &str,
    channel_id: &str,
    channel_epoch: u64,
    received_at_unix_nanos: UnixNanos,
    text: &str,
) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError> {
    let value: Value = serde_json::from_str(text)
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
    match value.get("e").and_then(Value::as_str) {
        Some("listenKeyExpired") => {
            return Err(IntegrationError::ResyncRequired(
                "Binance Options listen key expired".into(),
            ));
        }
        Some("ORDER_TRADE_UPDATE") => {}
        _ => return Ok(None),
    }
    let row = value.get("o").ok_or_else(|| {
        IntegrationError::InvalidPayload("Binance Options order update is missing o".into())
    })?;
    let required = |field: &str| {
        row.get(field)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| {
                IntegrationError::InvalidPayload(format!(
                    "Binance Options order update is missing {field}"
                ))
            })
    };
    let event_time_millis = value.get("E").and_then(Value::as_u64).unwrap_or_default();
    let trade_time_millis = row
        .get("T")
        .and_then(Value::as_u64)
        .unwrap_or(event_time_millis);
    let remote_order_id = row.get("i").map(value_string).unwrap_or_default();
    let trade_id = row.get("t").map(value_string).filter(|value| value != "0");
    let execution_type = row.get("x").and_then(Value::as_str).unwrap_or_default();
    let status_text = row.get("X").and_then(Value::as_str).unwrap_or_default();
    Ok(Some(ExternalEventEnvelope {
        participant: crate::domain::ParticipantRef::new(
            crate::domain::ParticipantKind::Exchange,
            "binance",
        )
        .expect("static Binance participant"),
        binding_id: binding_id.into(),
        channel_id: channel_id.into(),
        channel_epoch,
        provider_event_id: Some(format!(
            "binance-options:{remote_order_id}:{}:{execution_type}:{status_text}:{event_time_millis}",
            trade_id.as_deref().unwrap_or("none")
        )),
        provider_sequence: None,
        observed_at_unix_nanos: UnixNanos::from(event_time_millis.saturating_mul(1_000_000)),
        received_at_unix_nanos,
        payload: ExternalExecutionEvent {
            order_id: OrderId::new(required("c")?)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            symbol: Symbol::new(required("s")?)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            status: status(status_text),
            side: Some(if row.get("S").and_then(Value::as_str) == Some("SELL") {
                OrderSide::Sell
            } else {
                OrderSide::Buy
            }),
            order_type: order_type(row.get("o").and_then(Value::as_str)),
            quantity: decimal(row, "q")?,
            limit_price: non_zero_decimal(row, "p")?,
            filled_quantity: decimal(row, "z")?,
            remaining_quantity: None,
            fill_quantity: non_zero_decimal(row, "l")?,
            fill_price: non_zero_decimal(row, "L")?,
            execution_id: trade_id
                .map(|value| FillId::new(format!("binance-options:{value}")))
                .transpose()
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            fee_currency: row
                .get("N")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(Currency::new)
                .transpose()
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            fee_amount: non_zero_decimal(row, "n")?,
            occurred_at_unix_nanos: UnixNanos::from(trade_time_millis.saturating_mul(1_000_000)),
            reason: String::new(),
        },
    }))
}

fn status(value: &str) -> OrderStatus {
    match value {
        "NEW" | "ACCEPTED" => OrderStatus::Acknowledged,
        "PARTIALLY_FILLED" => OrderStatus::PartiallyFilled,
        "FILLED" => OrderStatus::Filled,
        "CANCELED" => OrderStatus::Canceled,
        "REJECTED" => OrderStatus::Rejected,
        "EXPIRED" => OrderStatus::Expired,
        _ => OrderStatus::Unknown,
    }
}

fn order_type(value: Option<&str>) -> Option<OrderType> {
    match value {
        Some("MARKET") => Some(OrderType::Market),
        Some("LIMIT") => Some(OrderType::Limit),
        _ => None,
    }
}

fn decimal(value: &Value, field: &str) -> Result<Option<DecimalValue>, IntegrationError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(DecimalValue::parse)
        .transpose()
        .map_err(IntegrationError::InvalidPayload)
}

fn non_zero_decimal(value: &Value, field: &str) -> Result<Option<DecimalValue>, IntegrationError> {
    Ok(decimal(value, field)?.filter(|value| value.mantissa != 0))
}

fn value_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};

    use futures_util::SinkExt;
    use kairos_primitives::{OrderStatus, UnixNanos};

    use crate::application::AsyncOrderEventSource;

    use super::{parse_order_trade_update, BinanceOptionsAsyncOrderEventSource};
    use crate::services::participants::binance::options::account::BinanceOptionsAccountClient;

    #[test]
    fn normalizes_options_order_trade_update_with_stable_identity() {
        let event = parse_order_trade_update(
            "execution.binance.options.primary",
            "orders",
            3,
            UnixNanos::from(2_000_000_000),
            r#"{"e":"ORDER_TRADE_UPDATE","E":1000,"T":999,"o":{"s":"BTC-260327-100000-C","c":"order-1","S":"BUY","o":"LIMIT","q":"0.25","p":"100","ap":"99.5","i":42,"t":7,"x":"TRADE","X":"PARTIALLY_FILLED","z":"0.1","l":"0.1","L":"99.5","n":"-0.01","N":"USDT","T":998}}"#,
        )
        .unwrap()
        .unwrap();
        assert_eq!(event.channel_epoch, 3);
        assert_eq!(event.payload.status, OrderStatus::PartiallyFilled);
        assert_eq!(
            event.payload.execution_id.unwrap().as_str(),
            "binance-options:7"
        );
        assert_eq!(
            event.provider_event_id.as_deref(),
            Some("binance-options:42:7:TRADE:PARTIALLY_FILLED:1000")
        );
        assert_eq!(event.payload.occurred_at_unix_nanos.get(), 998_000_000);
    }

    #[test]
    fn listen_key_expiry_requires_reconciliation() {
        let error = parse_order_trade_update(
            "binding",
            "channel",
            1,
            UnixNanos::from(0),
            r#"{"e":"listenKeyExpired","E":1000}"#,
        )
        .unwrap_err();
        assert!(matches!(
            error,
            crate::application::IntegrationError::ResyncRequired(_)
        ));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn async_source_creates_listen_key_and_receives_order_event() {
        let http_listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let http_address = http_listener.local_addr().unwrap();
        let http_server = std::thread::spawn(move || {
            let (mut stream, _) = http_listener.accept().unwrap();
            let mut request = [0_u8; 2_048];
            let read = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..read]);
            assert!(request.starts_with("POST /eapi/v1/listenKey "));
            assert!(request
                .to_ascii_lowercase()
                .contains("x-mbx-apikey: api-key"));
            let body = r#"{"listenKey":"test-options-listen-key"}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .unwrap();
        });

        let ws_listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let ws_address = ws_listener.local_addr().unwrap();
        let ws_server = tokio::spawn(async move {
            let (stream, _) = ws_listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            socket
                .send(tokio_tungstenite::tungstenite::Message::Text(
                    r#"{"e":"ORDER_TRADE_UPDATE","E":1000,"o":{"s":"BTC-260327-100000-C","c":"order-1","S":"BUY","o":"MARKET","q":"0.25","p":"0","i":42,"t":7,"x":"TRADE","X":"FILLED","z":"0.25","l":"0.25","L":"100.5","n":"-0.01","N":"USDT","T":999}}"#.into(),
                ))
                .await
                .unwrap();
        });

        let client =
            BinanceOptionsAccountClient::new("api-key", "secret", format!("http://{http_address}"))
                .unwrap();
        let mut source = BinanceOptionsAsyncOrderEventSource::new(
            "execution.binance.options.test",
            client,
            format!("ws://{ws_address}/private/stream"),
            8,
        )
        .unwrap();
        let event = source.next_order_event().await.unwrap();

        assert_eq!(event.channel_epoch, 1);
        assert_eq!(event.payload.status, OrderStatus::Filled);
        source.disconnect_channel().await.unwrap();
        ws_server.await.unwrap();
        http_server.join().unwrap();
    }
}
