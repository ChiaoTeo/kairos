//! Shared async Binance Spot authenticated user-data channel.
//!
//! The channel owns transport/authentication/reconnect state. Capability
//! projections decide how raw user-data messages are normalized; this keeps
//! transport/authentication semantics identical without making Integration
//! aware of Account or Execution business bundles.

use crate::application::capabilities::{ConnectionHealth, ConnectionLifecycle};
use crate::application::IntegrationError;
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};

use super::account::BinanceSpotAccountClient;
use super::order_events::{map_exchange_error, parse_subscription_response};

pub(crate) struct BinanceSpotAsyncUserDataChannel {
    binding_id: String,
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

impl BinanceSpotAsyncUserDataChannel {
    pub(crate) fn new(
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
                "Binance Spot user-data queue capacity must be positive".into(),
            ));
        }
        Ok(Self {
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

    pub(crate) fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            authenticated: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            last_error: self.last_error.clone(),
        }
    }

    pub(crate) async fn connect(&mut self) -> Result<(), IntegrationError> {
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

    pub(crate) async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.lifecycle = ConnectionLifecycle::Stopping;
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.subscription_id = None;
        self.auth_generation = None;
        self.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    pub(crate) async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.disconnect().await?;
        self.connect().await
    }

    /// Await the next raw text message. Ping/pong and non-text frames are
    /// handled internally and never surface as a fake "no event" result.
    pub(crate) async fn next_text(&mut self) -> Result<String, IntegrationError> {
        self.connect().await?;
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
            let socket = self.socket.as_mut().ok_or(IntegrationError::NotReady)?;
            let result = match socket.next_event().await {
                AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Text(text)) => {
                    Some(Ok(text.to_string()))
                }
                AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Ping(
                    payload,
                )) => {
                    socket
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                    None
                }
                AsyncSocketEvent::Message(tokio_tungstenite::tungstenite::Message::Close(
                    frame,
                )) => Some(Err(IntegrationError::Transport(format!(
                    "Binance Spot user stream closed: {frame:?}"
                )))),
                AsyncSocketEvent::Message(_) => None,
                AsyncSocketEvent::Error(error) => Some(Err(IntegrationError::Transport(error))),
                AsyncSocketEvent::Backpressure => Some(Err(IntegrationError::Backpressure(
                    "Binance Spot user-data queue overflowed; snapshot reconciliation is required"
                        .into(),
                ))),
            };
            if let Some(result) = result {
                if let Err(error) = &result {
                    self.record_failure(error);
                }
                return result;
            }
        }
    }

    fn record_failure(&mut self, error: &IntegrationError) {
        self.lifecycle = ConnectionLifecycle::Degraded;
        self.last_error = Some(error.to_string());
    }

    async fn subscribe(
        &self,
        socket: &mut AsyncTokioSocket,
    ) -> Result<(u64, u64), IntegrationError> {
        let request_id = format!(
            "{}:{}",
            self.binding_id,
            self.channel_epoch.saturating_add(1)
        );
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
                            "Binance Spot user-data queue overflowed during authentication".into(),
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
}
