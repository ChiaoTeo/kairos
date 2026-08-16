//! Async Binance Futures balance/position/order/fill channel.

use crate::application::capabilities::account_facts::{
    ExternalAccountEvent, ExternalAccountEventEnvelope,
};
use crate::application::{AsyncAccountEventSource, ExternalEventEnvelope, IntegrationError};
use crate::domain::{ConnectionHealth, ConnectionLifecycle, ParticipantKind, ParticipantRef};
use crate::services::transport::http::ExchangeError;
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};

use super::account::{parse_user_event, BinanceFuturesAccountClient};

pub(crate) struct BinanceFuturesAsyncAccountEventSource {
    binding_id: String,
    channel_id: String,
    segment_key: String,
    client: BinanceFuturesAccountClient,
    websocket_endpoint: String,
    event_queue_capacity: usize,
    socket: Option<AsyncTokioSocket>,
    listen_key: Option<String>,
    next_keepalive: Option<tokio::time::Instant>,
    lifecycle: ConnectionLifecycle,
    channel_epoch: u64,
    last_error: Option<String>,
}

impl BinanceFuturesAsyncAccountEventSource {
    pub(crate) fn new(
        binding_id: impl Into<String>,
        segment_key: impl Into<String>,
        client: BinanceFuturesAccountClient,
        websocket_endpoint: impl Into<String>,
        event_queue_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        let binding_id = binding_id.into();
        let segment_key = segment_key.into();
        let websocket_endpoint = websocket_endpoint.into().trim_end_matches('/').to_owned();
        if binding_id.trim().is_empty() || segment_key.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Futures account stream requires binding and segment ids".into(),
            ));
        }
        if !(websocket_endpoint.starts_with("wss://") || websocket_endpoint.starts_with("ws://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Futures account stream endpoint must start with ws:// or wss://".into(),
            ));
        }
        if event_queue_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Binance Futures account event queue capacity must be positive".into(),
            ));
        }
        Ok(Self {
            channel_id: format!("{binding_id}.account-events"),
            binding_id,
            segment_key,
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

impl AsyncAccountEventSource for BinanceFuturesAsyncAccountEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some() {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        let listen_key = tokio::time::timeout(
            std::time::Duration::from_secs(10),
            self.client.listen_key_async(),
        )
        .await
        .map_err(|_| {
            IntegrationError::Unavailable("timed out creating Binance Futures listen key".into())
        })?
        .map_err(map_exchange_error)?;
        let endpoint = format!("{}/ws/{listen_key}", self.websocket_endpoint);
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
                    "timed out connecting Binance Futures account stream".into(),
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

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.disconnect_channel().await?;
        self.connect_channel().await
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            authenticated: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            last_error: self.last_error.clone(),
        }
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<ExternalAccountEventEnvelope, IntegrationError> {
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
                        self.client
                            .keepalive_listen_key_async(listen_key)
                            .await
                            .map_err(map_exchange_error)?;
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
                        "Binance Futures account stream closed: {frame:?}"
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
                        "Binance Futures account event queue overflowed; resync is required".into(),
                    );
                    self.record_failure(&error);
                    return Err(error);
                }
            };
            if text.contains("\"e\":\"listenKeyExpired\"") {
                let error = IntegrationError::ResyncRequired(
                    "Binance Futures account listen key expired".into(),
                );
                self.record_failure(&error);
                return Err(error);
            }
            if let Some(payload) = parse_user_event(&self.segment_key, &text)
                .map_err(IntegrationError::InvalidPayload)?
            {
                let observed_at_unix_nanos = account_event_time(&payload);
                return Ok(ExternalEventEnvelope {
                    participant: ParticipantRef::new(ParticipantKind::Exchange, "binance")
                        .expect("static Binance participant"),
                    binding_id: self.binding_id.clone(),
                    channel_id: self.channel_id.clone(),
                    channel_epoch: self.channel_epoch,
                    provider_event_id: provider_event_id(&payload),
                    provider_sequence: None,
                    observed_at_unix_nanos,
                    received_at_unix_nanos: now_unix_nanos(),
                    payload,
                });
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

fn account_event_time(event: &ExternalAccountEvent) -> kairos_primitives::UnixNanos {
    match event {
        ExternalAccountEvent::Snapshot(value) => value.observed_at_unix_nanos,
        ExternalAccountEvent::Order(value) => value.occurred_at_unix_nanos,
        ExternalAccountEvent::Fill(value) => value.occurred_at_unix_nanos,
        ExternalAccountEvent::Batch(values) => values
            .iter()
            .map(account_event_time)
            .max()
            .unwrap_or_else(|| 0.into()),
    }
}

fn provider_event_id(event: &ExternalAccountEvent) -> Option<String> {
    match event {
        ExternalAccountEvent::Fill(value) => {
            Some(format!("binance-futures:fill:{}", value.fill_id))
        }
        ExternalAccountEvent::Order(value) => Some(format!(
            "binance-futures:order:{}:{}",
            value.order_id,
            value.occurred_at_unix_nanos.get()
        )),
        ExternalAccountEvent::Snapshot(_) | ExternalAccountEvent::Batch(_) => None,
    }
}

fn now_unix_nanos() -> kairos_primitives::UnixNanos {
    kairos_primitives::UnixNanos::new(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .min(u64::MAX as u128) as u64,
    )
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};

    use futures_util::SinkExt;

    use crate::application::capabilities::account_facts::ExternalAccountEvent;
    use crate::application::AsyncAccountEventSource;
    use crate::services::participants::binance::ConnectionDomain;

    use super::{BinanceFuturesAccountClient, BinanceFuturesAsyncAccountEventSource};

    #[tokio::test(flavor = "current_thread")]
    async fn account_event_uses_the_callers_runtime_and_preserves_binding() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let http_server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 2_048];
            let read = stream.read(&mut request).unwrap();
            assert!(
                String::from_utf8_lossy(&request[..read]).starts_with("POST /fapi/v1/listenKey ")
            );
            let body = r#"{"listenKey":"account-listen-key"}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });
        let websocket = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let websocket_address = websocket.local_addr().unwrap();
        let websocket_server = tokio::spawn(async move {
            let (stream, _) = websocket.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            socket
                .send(tokio_tungstenite::tungstenite::Message::Text(
                    serde_json::json!({
                        "e": "ACCOUNT_UPDATE",
                        "E": 1_700_000_000_000_u64,
                        "a": {
                            "B": [{"a": "USDT", "wb": "12.5", "cw": "12.0"}],
                            "P": []
                        }
                    })
                    .to_string()
                    .into(),
                ))
                .await
                .unwrap();
        });
        let client = BinanceFuturesAccountClient::new(
            ConnectionDomain::UsdMFutures,
            "api-key",
            "secret",
            format!("http://{address}"),
        )
        .unwrap();
        let mut source = BinanceFuturesAsyncAccountEventSource::new(
            "account.binance.usdm",
            "usd-m-futures",
            client,
            format!("ws://{websocket_address}"),
            8,
        )
        .unwrap();

        let event = source.next_account_event().await.unwrap();
        assert_eq!(event.binding_id, "account.binance.usdm");
        assert_eq!(event.channel_epoch, 1);
        let ExternalAccountEvent::Snapshot(snapshot) = event.payload else {
            panic!("expected account snapshot delta");
        };
        assert!(snapshot.partial);
        assert_eq!(snapshot.balances.len(), 1);
        source.disconnect_channel().await.unwrap();
        http_server.join().unwrap();
        websocket_server.await.unwrap();
    }
}
