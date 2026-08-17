//! Binance Spot private order/fill event channel.

use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::{Currency, FillId, OrderId, Symbol, UnixNanos};
use serde_json::Value;

use crate::application::capabilities::{
    execution_facts::normalize_order_status, ConnectionHealth, ConnectionLifecycle, DecimalValue,
    OrderSide, OrderType,
};
use crate::application::{
    ExternalEventEnvelope, ExternalExecutionEvent, IntegrationError, OrderEventSource,
};
use crate::services::transport::http::ExchangeError;
use crate::services::transport::websocket::{SocketEvent, TokioSocket};

use super::account::BinanceSpotAccountClient;

pub(crate) struct BinanceSpotOrderEventSource {
    binding_id: String,
    channel_id: String,
    client: BinanceSpotAccountClient,
    websocket_endpoint: String,
    event_queue_capacity: usize,
    socket: Option<TokioSocket>,
    subscription_id: Option<u64>,
    auth_generation: Option<u64>,
    lifecycle: ConnectionLifecycle,
    channel_epoch: u64,
    last_error: Option<String>,
}

impl BinanceSpotOrderEventSource {
    #[cfg(test)]
    pub(crate) fn from_client(
        binding_id: impl Into<String>,
        client: BinanceSpotAccountClient,
        websocket_endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        Self::from_client_with_capacity(binding_id, client, websocket_endpoint, 1_024)
    }

    pub(crate) fn from_client_with_capacity(
        binding_id: impl Into<String>,
        client: BinanceSpotAccountClient,
        websocket_endpoint: impl Into<String>,
        event_queue_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        let binding_id = binding_id.into();
        if binding_id.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot binding id is required".into(),
            ));
        }
        let websocket_endpoint = websocket_endpoint.into().trim_end_matches('/').to_string();
        if !(websocket_endpoint.starts_with("wss://") || websocket_endpoint.starts_with("ws://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot user stream endpoint must start with ws:// or wss://".into(),
            ));
        }
        if event_queue_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot order event queue capacity must be positive".into(),
            ));
        }
        Ok(Self {
            channel_id: format!("{binding_id}.order-events"),
            binding_id,
            client,
            websocket_endpoint,
            event_queue_capacity,
            socket: None,
            subscription_id: None,
            auth_generation: None,
            lifecycle: ConnectionLifecycle::Created,
            channel_epoch: 0,
            last_error: None,
        })
    }

    fn record_failure(&mut self, error: &IntegrationError) {
        self.lifecycle = ConnectionLifecycle::Degraded;
        self.last_error = Some(error.to_string());
    }

    fn next_text(&mut self) -> Result<Option<String>, IntegrationError> {
        let event = self
            .socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .try_recv()
            .map_err(IntegrationError::Transport)?;
        let Some(event) = event else {
            return Ok(None);
        };
        match event {
            SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Text(text)) => {
                Ok(Some(text.to_string()))
            }
            SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Ping(payload)) => {
                self.socket
                    .as_ref()
                    .ok_or(IntegrationError::NotReady)?
                    .send_pong(payload.to_vec())
                    .map_err(IntegrationError::Transport)?;
                Ok(None)
            }
            SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Close(frame)) => Err(
                IntegrationError::Transport(format!("Binance Spot user stream closed: {frame:?}")),
            ),
            SocketEvent::Message(_) => Ok(None),
            SocketEvent::Error(error) => Err(IntegrationError::Transport(error)),
            SocketEvent::Backpressure => Err(IntegrationError::Backpressure(
                "Binance Spot order event queue overflowed; reconciliation is required".into(),
            )),
        }
    }

    fn subscribe(&mut self, socket: &TokioSocket) -> Result<(u64, u64), IntegrationError> {
        let request_id =
            provider_safe_request_id(&self.binding_id, self.channel_epoch.saturating_add(1));
        let (request, auth_generation) = self
            .client
            .user_data_subscription_request(&request_id)
            .map_err(map_exchange_error)?;
        socket
            .send_text(request)
            .map_err(IntegrationError::Transport)?;
        loop {
            match socket
                .recv_timeout(std::time::Duration::from_secs(10))
                .map_err(IntegrationError::Transport)?
                .ok_or_else(|| {
                    IntegrationError::Transport(
                        "timed out waiting for Binance Spot subscription response".into(),
                    )
                })? {
                SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Text(text)) => {
                    if let Some(subscription_id) =
                        parse_subscription_response(&request_id, text.as_ref())?
                    {
                        return Ok((subscription_id, auth_generation));
                    }
                }
                SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Ping(payload)) => {
                    socket
                        .send_pong(payload.to_vec())
                        .map_err(IntegrationError::Transport)?;
                }
                SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Close(frame)) => {
                    return Err(IntegrationError::Transport(format!(
                        "Binance Spot WebSocket API closed during subscription: {frame:?}"
                    )))
                }
                SocketEvent::Message(_) => {}
                SocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
                SocketEvent::Backpressure => {
                    return Err(IntegrationError::Backpressure(
                        "Binance Spot order event queue overflowed during authentication".into(),
                    ))
                }
            }
        }
    }
}

impl OrderEventSource for BinanceSpotOrderEventSource {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some() {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        match TokioSocket::connect_with_event_capacity(
            self.websocket_endpoint.clone(),
            self.event_queue_capacity,
        ) {
            Ok(socket) => match self.subscribe(&socket) {
                Ok((subscription_id, auth_generation)) => {
                    self.socket = Some(socket);
                    self.subscription_id = Some(subscription_id);
                    self.auth_generation = Some(auth_generation);
                    self.channel_epoch = self.channel_epoch.saturating_add(1);
                    self.lifecycle = ConnectionLifecycle::Ready;
                    self.last_error = None;
                    Ok(())
                }
                Err(error) => {
                    self.record_failure(&error);
                    Err(error)
                }
            },
            Err(message) => {
                let error = IntegrationError::Transport(message);
                self.record_failure(&error);
                Err(error)
            }
        }
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.lifecycle = ConnectionLifecycle::Stopping;
        self.socket.take();
        self.subscription_id = None;
        self.auth_generation = None;
        self.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.disconnect_channel()?;
        self.connect_channel()
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            authenticated: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            last_error: self.last_error.clone(),
        }
    }

    fn try_next_order_event(
        &mut self,
    ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError> {
        self.connect_channel()?;
        if self.auth_generation
            != Some(
                self.client
                    .credential_generation()
                    .map_err(map_exchange_error)?,
            )
        {
            let error = IntegrationError::ResyncRequired(
                "Binance Spot credentials changed; reconnect the private channel".into(),
            );
            self.record_failure(&error);
            return Err(error);
        }
        let text = match self.next_text() {
            Ok(Some(text)) => text,
            Ok(None) => return Ok(None),
            Err(error) => {
                self.record_failure(&error);
                return Err(error);
            }
        };
        let received_at_unix_nanos = UnixNanos::from(now_unix_nanos());
        parse_execution_report(
            &self.binding_id,
            &self.channel_id,
            self.channel_epoch,
            received_at_unix_nanos,
            &text,
        )
    }
}

pub(in crate::services::participants::binance) fn parse_execution_report(
    binding_id: &str,
    channel_id: &str,
    channel_epoch: u64,
    received_at_unix_nanos: UnixNanos,
    text: &str,
) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError> {
    let outer: Value = serde_json::from_str(text)
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
    let value = outer.get("event").unwrap_or(&outer);
    match value.get("e").and_then(Value::as_str) {
        Some("eventStreamTerminated") => {
            return Err(IntegrationError::ResyncRequired(
                "Binance Spot user data subscription terminated".into(),
            ))
        }
        Some("serverShutdown") => {
            return Err(IntegrationError::Unavailable(
                "Binance Spot WebSocket API server is shutting down".into(),
            ))
        }
        Some("executionReport") => {}
        _ => return Ok(None),
    }
    if value.get("e").and_then(Value::as_str) != Some("executionReport") {
        return Ok(None);
    }
    (|| {
        let local_order_id = required_str(value, "c", "client order id")?;
        let symbol = required_str(&value, "s", "symbol")?;
        let event_time_millis = value.get("E").and_then(Value::as_u64).unwrap_or_default();
        let observed_at_unix_nanos = UnixNanos::from(event_time_millis.saturating_mul(1_000_000));
        let trade_id = value.get("t").map(value_as_string).filter(|id| id != "-1");
        let execution_id = trade_id
            .as_deref()
            .map(|id| FillId::new(format!("binance:{id}")))
            .transpose()?;
        let provider_execution_id = value.get("I").map(value_as_string);
        let provider_order_id = value.get("i").map(value_as_string).unwrap_or_default();
        let status = value.get("X").and_then(Value::as_str).unwrap_or_default();
        let provider_event_id = Some(format!(
            "binance:{provider_order_id}:{}:{status}:{event_time_millis}",
            provider_execution_id
                .or(trade_id)
                .unwrap_or_else(|| "none".into())
        ));
        let quantity = optional_decimal(&value, "q")?;
        let filled_quantity = optional_decimal(&value, "z")?;
        let fill_quantity = optional_non_zero_decimal(&value, "l")?;
        let fill_price = optional_non_zero_decimal(&value, "L")?;
        let fee_amount = optional_non_zero_decimal(&value, "n")?;
        let fee_currency = value
            .get("N")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(Currency::new)
            .transpose()?;
        Ok(Some(ExternalEventEnvelope {
            participant: crate::domain::ParticipantRef::new(
                crate::domain::ParticipantKind::Exchange,
                "binance",
            )
            .expect("static Binance participant is valid"),
            binding_id: binding_id.into(),
            channel_id: channel_id.into(),
            channel_epoch,
            provider_event_id,
            provider_sequence: None,
            observed_at_unix_nanos,
            received_at_unix_nanos,
            payload: ExternalExecutionEvent {
                order_id: OrderId::new(local_order_id)?,
                symbol: Symbol::new(symbol)?,
                status: normalize_order_status(status),
                side: Some(match value.get("S").and_then(Value::as_str) {
                    Some("SELL") => OrderSide::Sell,
                    _ => OrderSide::Buy,
                }),
                order_type: value
                    .get("o")
                    .and_then(Value::as_str)
                    .and_then(normalize_order_type),
                quantity,
                limit_price: optional_non_zero_decimal(&value, "p")?,
                filled_quantity,
                remaining_quantity: None,
                fill_quantity,
                fill_price,
                execution_id,
                fee_currency,
                fee_amount,
                occurred_at_unix_nanos: observed_at_unix_nanos,
                reason: value
                    .get("r")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .into(),
            },
        }))
    })()
    .map_err(IntegrationError::InvalidPayload)
}

pub(super) fn parse_subscription_response(
    request_id: &str,
    text: &str,
) -> Result<Option<u64>, IntegrationError> {
    let value: Value = serde_json::from_str(text)
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
    if value.get("id").and_then(Value::as_str) != Some(request_id) {
        return Ok(None);
    }
    let status = value
        .get("status")
        .and_then(Value::as_u64)
        .unwrap_or_default();
    if status == 200 {
        return value
            .pointer("/result/subscriptionId")
            .and_then(Value::as_u64)
            .map(Some)
            .ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Binance user data subscription response is missing subscriptionId".into(),
                )
            });
    }
    let code = value.pointer("/error/code").map(value_as_string);
    let message = value
        .pointer("/error/msg")
        .and_then(Value::as_str)
        .unwrap_or("Binance user data subscription was rejected")
        .to_string();
    match status {
        401 => Err(IntegrationError::Authentication(message)),
        403 => Err(IntegrationError::Authorization(message)),
        418 | 429 => Err(IntegrationError::RateLimited(message)),
        _ => Err(IntegrationError::Unavailable(format!(
            "{}: {}",
            code.unwrap_or_else(|| status.to_string()),
            message
        ))),
    }
}

/// Binance WebSocket string request IDs are limited to 36 characters. Its
/// production gateway also closes signed user-data subscriptions when IDs
/// contain punctuation such as `.` or `:`. Keep adapter-generated IDs inside
/// both provider constraints while retaining the channel epoch suffix.
pub(super) fn provider_safe_request_id(binding_id: &str, channel_epoch: u64) -> String {
    const MAX_REQUEST_ID_LEN: usize = 36;

    let mut normalized = String::with_capacity(binding_id.len().min(MAX_REQUEST_ID_LEN));
    let mut previous_was_separator = false;
    for character in binding_id.chars() {
        let character = if character.is_ascii_alphanumeric() {
            previous_was_separator = false;
            character
        } else if previous_was_separator {
            continue;
        } else {
            previous_was_separator = true;
            '-'
        };
        normalized.push(character);
    }
    let normalized = normalized.trim_matches('-');
    let normalized = if normalized.is_empty() {
        "kairos"
    } else {
        normalized
    };
    let suffix = format!("-{channel_epoch}");
    let prefix_len = MAX_REQUEST_ID_LEN.saturating_sub(suffix.len());
    let prefix = &normalized[..normalized.len().min(prefix_len)];
    format!("{prefix}{suffix}")
}

fn normalize_order_type(value: &str) -> Option<OrderType> {
    match value {
        "MARKET" => Some(OrderType::Market),
        "LIMIT" | "LIMIT_MAKER" => Some(OrderType::Limit),
        "STOP_LOSS" | "TAKE_PROFIT" => Some(OrderType::Stop),
        "STOP_LOSS_LIMIT" | "TAKE_PROFIT_LIMIT" => Some(OrderType::StopLimit),
        _ => None,
    }
}

fn required_str(value: &Value, key: &str, field: &str) -> Result<String, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| format!("Binance execution report {field} is missing"))
}

fn optional_decimal(value: &Value, key: &str) -> Result<Option<DecimalValue>, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(DecimalValue::parse)
        .transpose()
}

fn optional_non_zero_decimal(value: &Value, key: &str) -> Result<Option<DecimalValue>, String> {
    optional_decimal(value, key).map(|value| value.filter(|value| value.mantissa != 0))
}

fn value_as_string(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}

pub(in crate::services::participants::binance) fn map_exchange_error(
    error: ExchangeError,
) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::Http { status: 401, body } => IntegrationError::Authentication(body),
        ExchangeError::Http { status: 403, body } => IntegrationError::Authorization(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::{
        parse_execution_report, parse_subscription_response, provider_safe_request_id,
        BinanceSpotOrderEventSource,
    };
    use crate::application::capabilities::{OrderSide, OrderType};
    use crate::application::OrderEventSource;
    use crate::services::participants::binance::spot::account::BinanceSpotAccountClient;
    use futures_util::{SinkExt, StreamExt};
    use kairos_primitives::{OrderStatus, UnixNanos};

    #[test]
    fn parses_execution_report_with_stable_envelope_and_fill() {
        let envelope = parse_execution_report(
            "execution.binance.spot.primary",
            "execution.binance.spot.primary.order-events",
            3,
            UnixNanos::from(1_100_000_000),
            r#"{"subscriptionId":12,"event":{"e":"executionReport","E":1000,"s":"BTCUSDT","c":"order-1","S":"BUY","o":"LIMIT","q":"0.25","p":"100.5","i":42,"I":99,"t":7,"X":"PARTIALLY_FILLED","z":"0.10","l":"0.10","L":"100.5","n":"0.01","N":"USDT","r":"NONE"}}"#,
        )
        .unwrap()
        .unwrap();

        assert_eq!(envelope.binding_id, "execution.binance.spot.primary");
        assert_eq!(envelope.channel_epoch, 3);
        assert_eq!(
            envelope.provider_event_id.as_deref(),
            Some("binance:42:99:PARTIALLY_FILLED:1000")
        );
        assert_eq!(envelope.payload.order_id, "order-1");
        assert_eq!(envelope.payload.symbol, "BTCUSDT");
        assert_eq!(envelope.payload.status, OrderStatus::PartiallyFilled);
        assert_eq!(envelope.payload.side, Some(OrderSide::Buy));
        assert_eq!(envelope.payload.order_type, Some(OrderType::Limit));
        assert_eq!(envelope.payload.fill_quantity.unwrap().mantissa, 10);
        assert_eq!(envelope.payload.execution_id.as_deref(), Some("binance:7"));
        assert_eq!(envelope.payload.fee_currency.as_deref(), Some("USDT"));
    }

    #[test]
    fn ignores_non_order_user_events() {
        assert!(parse_execution_report(
            "binding",
            "channel",
            1,
            UnixNanos::from(2),
            r#"{"e":"outboundAccountPosition","E":1,"B":[]}"#,
        )
        .unwrap()
        .is_none());
    }

    #[test]
    fn accepts_only_matching_successful_subscription_response() {
        assert_eq!(
            parse_subscription_response(
                "request-1",
                r#"{"id":"request-1","status":200,"result":{"subscriptionId":12}}"#,
            )
            .unwrap(),
            Some(12)
        );
        assert_eq!(
            parse_subscription_response(
                "request-1",
                r#"{"id":"another-request","status":200,"result":{"subscriptionId":13}}"#,
            )
            .unwrap(),
            None
        );
    }

    #[test]
    fn generates_provider_safe_subscription_request_id() {
        let request_id = provider_safe_request_id("account.binance.spot:primary", 7);
        assert_eq!(request_id, "account-binance-spot-primary-7");
        assert!(request_id
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '-'));
        assert_eq!(provider_safe_request_id("...", 1), "kairos-1");
        assert_eq!(
            provider_safe_request_id("account.binance.spot.account-events", 1),
            "account-binance-spot-account-event-1"
        );
        assert!(provider_safe_request_id(&"x".repeat(100), u64::MAX).len() <= 36);
    }

    #[test]
    fn classifies_subscription_rate_limit() {
        let error = parse_subscription_response(
            "request-1",
            r#"{"id":"request-1","status":429,"error":{"code":-1003,"msg":"slow down"}}"#,
        )
        .unwrap_err();
        assert!(matches!(
            error,
            crate::application::IntegrationError::RateLimited(_)
        ));
    }

    #[test]
    fn websocket_api_signature_subscription_delivers_order_event() {
        let time_listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let time_address = time_listener.local_addr().unwrap();
        let time_server = std::thread::spawn(move || {
            use std::io::{Read, Write};

            let (mut stream, _) = time_listener.accept().unwrap();
            let mut request = [0_u8; 2_048];
            let read = stream.read(&mut request).unwrap();
            assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /api/v3/time "));
            let server_time = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis() as u64;
            let body = serde_json::json!({"serverTime": server_time}).to_string();
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            )
            .unwrap();
        });
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        listener.set_nonblocking(true).unwrap();
        let server = std::thread::spawn(move || {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .unwrap();
            runtime.block_on(async move {
                let listener = tokio::net::TcpListener::from_std(listener).unwrap();
                let (stream, _) = listener.accept().await.unwrap();
                let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
                let request = socket.next().await.unwrap().unwrap().into_text().unwrap();
                let request: serde_json::Value = serde_json::from_str(&request).unwrap();
                assert_eq!(
                    request.get("method").and_then(serde_json::Value::as_str),
                    Some("userDataStream.subscribe.signature")
                );
                assert_eq!(
                    request.pointer("/params/apiKey").and_then(serde_json::Value::as_str),
                    Some("api-key")
                );
                assert!(request
                    .pointer("/params/signature")
                    .and_then(serde_json::Value::as_str)
                    .is_some_and(|value| !value.is_empty()));
                let request_id = request.get("id").cloned().unwrap();
                socket
                    .send(tokio_tungstenite::tungstenite::Message::Text(
                        serde_json::json!({
                            "id": request_id,
                            "status": 200,
                            "result": {"subscriptionId": 7}
                        })
                        .to_string()
                        .into(),
                    ))
                    .await
                    .unwrap();
                socket
                    .send(tokio_tungstenite::tungstenite::Message::Text(
                        r#"{"subscriptionId":7,"event":{"e":"executionReport","E":1000,"s":"BTCUSDT","c":"order-1","S":"BUY","o":"MARKET","q":"0.25","p":"0","i":42,"I":99,"t":7,"X":"FILLED","z":"0.25","l":"0.25","L":"100.5","n":"0.01","N":"USDT","r":"NONE"}}"#
                            .into(),
                    ))
                    .await
                    .unwrap();
            });
        });

        let client = BinanceSpotAccountClient::new(
            "api-key",
            "api-secret",
            format!("http://{time_address}"),
        )
        .unwrap();
        let credential_control = client.clone();
        let mut source = BinanceSpotOrderEventSource::from_client(
            "execution.binance.spot.test",
            client,
            format!("ws://{address}"),
        )
        .unwrap();
        source.connect_channel().unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        let envelope = loop {
            if let Some(envelope) = source.try_next_order_event().unwrap() {
                break envelope;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "order event timed out"
            );
            std::thread::sleep(std::time::Duration::from_millis(5));
        };
        assert_eq!(envelope.channel_epoch, 1);
        assert_eq!(envelope.payload.status, OrderStatus::Filled);
        credential_control
            .rotate_credentials("rotated-key", "rotated-secret")
            .unwrap();
        assert!(matches!(
            source.try_next_order_event(),
            Err(crate::application::IntegrationError::ResyncRequired(_))
        ));
        server.join().unwrap();
        time_server.join().unwrap();
    }
}
