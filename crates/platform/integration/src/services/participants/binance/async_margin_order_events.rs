//! Async Binance cross/isolated margin private order channel.

use std::time::{SystemTime, UNIX_EPOCH};

use kairos_domain_types::UnixNanos;

use crate::application::capabilities::{ConnectionHealth, ConnectionLifecycle};
use crate::application::{
    AsyncOrderEventSource, ExternalEventEnvelope, ExternalExecutionEvent, IntegrationError,
};
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};

use super::spot::account::BinanceSpotAccountClient;
use super::spot::order_events::{map_exchange_error, parse_execution_report};

pub(crate) struct BinanceAsyncMarginOrderEventSource {
    binding_id: String,
    channel_id: String,
    client: BinanceSpotAccountClient,
    websocket_endpoint: String,
    isolated_symbol: Option<String>,
    event_queue_capacity: usize,
    socket: Option<AsyncTokioSocket>,
    listen_key: Option<String>,
    auth_generation: Option<u64>,
    next_keepalive: Option<tokio::time::Instant>,
    lifecycle: ConnectionLifecycle,
    channel_epoch: u64,
    last_error: Option<String>,
}

impl BinanceAsyncMarginOrderEventSource {
    pub(crate) fn new(
        binding_id: impl Into<String>,
        client: BinanceSpotAccountClient,
        websocket_endpoint: impl Into<String>,
        isolated_symbol: Option<String>,
        event_queue_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        let binding_id = binding_id.into();
        let websocket_endpoint = websocket_endpoint.into().trim_end_matches('/').to_owned();
        if binding_id.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Margin binding id is required".into(),
            ));
        }
        if !(websocket_endpoint.starts_with("wss://") || websocket_endpoint.starts_with("ws://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Margin stream endpoint must start with ws:// or wss://".into(),
            ));
        }
        if isolated_symbol
            .as_ref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err(IntegrationError::InvalidRequest(
                "Binance isolated-margin stream symbol must not be empty".into(),
            ));
        }
        if event_queue_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Binance Margin event queue capacity must be positive".into(),
            ));
        }
        Ok(Self {
            channel_id: format!("{binding_id}.order-events"),
            binding_id,
            client,
            websocket_endpoint,
            isolated_symbol,
            event_queue_capacity,
            socket: None,
            listen_key: None,
            auth_generation: None,
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

impl AsyncOrderEventSource for BinanceAsyncMarginOrderEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some() {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        let listen_key = match tokio::time::timeout(
            std::time::Duration::from_secs(10),
            self.client
                .margin_listen_key_async(self.isolated_symbol.as_deref()),
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
                    "timed out creating Binance Margin listen key".into(),
                );
                self.record_failure(&error);
                return Err(error);
            }
        };
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
                self.auth_generation = Some(
                    self.client
                        .credential_generation()
                        .map_err(map_exchange_error)?,
                );
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
                    "timed out connecting Binance Margin user stream".into(),
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
        self.auth_generation = None;
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
            let generation = self
                .client
                .credential_generation()
                .map_err(map_exchange_error)?;
            if self.auth_generation != Some(generation) {
                let error = IntegrationError::ResyncRequired(
                    "Binance Margin credentials changed; reconnect the private channel".into(),
                );
                self.record_failure(&error);
                return Err(error);
            }
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
                        if let Err(error) = self
                            .client
                            .keepalive_margin_listen_key_async(
                                listen_key,
                                self.isolated_symbol.as_deref(),
                            )
                            .await
                        {
                            let error = IntegrationError::ResyncRequired(format!(
                                "Binance Margin listen-key keepalive failed: {error}"
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
                        "Binance Margin user stream closed: {frame:?}"
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
                        "Binance Margin event queue overflowed; reconciliation is required".into(),
                    );
                    self.record_failure(&error);
                    return Err(error);
                }
            };
            if let Some(event) = parse_execution_report(
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
    use kairos_domain_types::OrderStatus;

    use crate::application::AsyncOrderEventSource;
    use crate::services::participants::binance::spot::account::BinanceSpotAccountClient;

    use super::BinanceAsyncMarginOrderEventSource;

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn cross_margin_source_uses_listen_key_and_normalizes_execution_report() {
        let http_listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let http_address = http_listener.local_addr().unwrap();
        let http_server = std::thread::spawn(move || {
            let (mut stream, _) = http_listener.accept().unwrap();
            let mut request = [0_u8; 2_048];
            let read = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..read]);
            assert!(request.starts_with("POST /sapi/v1/userDataStream "));
            let body = r#"{"listenKey":"margin-listen-key"}"#;
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
                    r#"{"e":"executionReport","E":1000,"s":"BTCUSDT","c":"margin-1","S":"SELL","o":"LIMIT","q":"0.5","p":"100","i":12,"I":13,"t":14,"X":"PARTIALLY_FILLED","z":"0.2","l":"0.2","L":"100","n":"0.01","N":"USDT","r":"NONE"}"#.into(),
                ))
                .await
                .unwrap();
        });
        let client =
            BinanceSpotAccountClient::new("api-key", "secret", format!("http://{http_address}"))
                .unwrap();
        let mut source = BinanceAsyncMarginOrderEventSource::new(
            "execution.binance.cross-margin.test",
            client,
            format!("ws://{ws_address}"),
            None,
            8,
        )
        .unwrap();

        let event = source.next_order_event().await.unwrap();

        assert_eq!(event.channel_epoch, 1);
        assert_eq!(event.payload.status, OrderStatus::PartiallyFilled);
        assert_eq!(event.binding_id, "execution.binance.cross-margin.test");
        source.disconnect_channel().await.unwrap();
        ws_server.await.unwrap();
        http_server.join().unwrap();
    }
}
