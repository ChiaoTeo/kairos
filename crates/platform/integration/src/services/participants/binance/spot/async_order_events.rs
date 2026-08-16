//! Async Binance Spot private order/fill channel.
//!
//! This is the primary provider path. It runs entirely on the Tokio runtime
//! polling the caller's async task and never creates a hidden runtime thread.

use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::UnixNanos;

use crate::application::capabilities::{ConnectionHealth, ConnectionLifecycle};
use crate::application::{
    AsyncOrderEventSource, ExternalEventEnvelope, ExternalExecutionEvent, IntegrationError,
};
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};

use super::account::BinanceSpotAccountClient;
use super::order_events::{
    map_exchange_error, parse_execution_report, parse_subscription_response,
    provider_safe_request_id,
};

pub(crate) struct BinanceSpotAsyncOrderEventSource {
    binding_id: String,
    channel_id: String,
    client: BinanceSpotAccountClient,
    websocket_endpoint: String,
    event_queue_capacity: usize,
    socket: Option<AsyncTokioSocket>,
    subscription_id: Option<u64>,
    auth_generation: Option<u64>,
    lifecycle: ConnectionLifecycle,
    channel_epoch: u64,
    last_error: Option<String>,
}

impl BinanceSpotAsyncOrderEventSource {
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

    async fn subscribe(
        &self,
        socket: &mut AsyncTokioSocket,
    ) -> Result<(u64, u64), IntegrationError> {
        let request_id =
            provider_safe_request_id(&self.binding_id, self.channel_epoch.saturating_add(1));
        let (request, auth_generation) = self
            .client
            .user_data_subscription_request_async(&request_id)
            .await
            .map_err(map_exchange_error)?;
        socket
            .send_text(request)
            .await
            .map_err(IntegrationError::Transport)?;
        let subscription_id = tokio::time::timeout(std::time::Duration::from_secs(10), async {
            loop {
                match socket.next_event().await {
                    AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Text(
                        text,
                    )) => {
                        if let Some(subscription_id) =
                            parse_subscription_response(&request_id, text.as_ref())?
                        {
                            return Ok(subscription_id);
                        }
                    }
                    AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Ping(
                        payload,
                    )) => socket
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?,
                    AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Close(
                        frame,
                    )) => {
                        return Err(IntegrationError::Transport(format!(
                            "Binance Spot WebSocket API closed during subscription: {frame:?}"
                        )))
                    }
                    AsyncSocketEvent::Message(_) => {}
                    AsyncSocketEvent::Error(error) => {
                        return Err(IntegrationError::Transport(error))
                    }
                    AsyncSocketEvent::Backpressure => {
                        return Err(IntegrationError::Backpressure(
                            "Binance Spot order event queue overflowed during authentication"
                                .into(),
                        ))
                    }
                }
            }
        })
        .await
        .map_err(|_| {
            IntegrationError::Transport(
                "timed out waiting for Binance Spot subscription response".into(),
            )
        })??;
        Ok((subscription_id, auth_generation))
    }

    async fn next_text(&mut self) -> Result<Option<String>, IntegrationError> {
        let socket = self.socket.as_mut().ok_or(IntegrationError::NotReady)?;
        match socket.next_event().await {
            AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Text(text)) => {
                Ok(Some(text.to_string()))
            }
            AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Ping(payload)) => {
                socket
                    .send_pong(payload.to_vec())
                    .await
                    .map_err(IntegrationError::Transport)?;
                Ok(None)
            }
            AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Close(frame)) => {
                Err(IntegrationError::Transport(format!(
                    "Binance Spot user stream closed: {frame:?}"
                )))
            }
            AsyncSocketEvent::Message(_) => Ok(None),
            AsyncSocketEvent::Error(error) => Err(IntegrationError::Transport(error)),
            AsyncSocketEvent::Backpressure => Err(IntegrationError::Backpressure(
                "Binance Spot order event queue overflowed; reconciliation is required".into(),
            )),
        }
    }
}

impl AsyncOrderEventSource for BinanceSpotAsyncOrderEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some() {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        let mut socket =
            match AsyncTokioSocket::connect(&self.websocket_endpoint, self.event_queue_capacity)
                .await
            {
                Ok(socket) => socket,
                Err(message) => {
                    let error = IntegrationError::Transport(message);
                    self.record_failure(&error);
                    return Err(error);
                }
            };
        match self.subscribe(&mut socket).await {
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
                socket.close().await;
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
        self.subscription_id = None;
        self.auth_generation = None;
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
            let text = match self.next_text().await {
                Ok(Some(text)) => text,
                Ok(None) => continue,
                Err(error) => {
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

    use futures_util::{SinkExt, StreamExt};
    use kairos_primitives::OrderStatus;

    use crate::application::AsyncOrderEventSource;
    use crate::services::participants::binance::spot::account::BinanceSpotAccountClient;

    use super::BinanceSpotAsyncOrderEventSource;

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn async_source_awaits_subscription_and_event_on_the_caller_runtime() {
        let time_listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let time_address = time_listener.local_addr().unwrap();
        let time_server = std::thread::spawn(move || {
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

        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            let request = socket.next().await.unwrap().unwrap().into_text().unwrap();
            let request: serde_json::Value = serde_json::from_str(&request).unwrap();
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
            while socket.next().await.is_some() {}
        });

        let client = BinanceSpotAccountClient::new(
            "api-key",
            "api-secret",
            format!("http://{time_address}"),
        )
        .unwrap();
        let mut source = BinanceSpotAsyncOrderEventSource::from_client_with_capacity(
            "execution.binance.spot.async-test",
            client,
            format!("ws://{address}"),
            8,
        )
        .unwrap();

        let envelope = source.next_order_event().await.unwrap();
        assert_eq!(envelope.channel_epoch, 1);
        assert_eq!(envelope.payload.status, OrderStatus::Filled);
        source.disconnect_channel().await.unwrap();
        server.await.unwrap();
        time_server.join().unwrap();
    }
}
