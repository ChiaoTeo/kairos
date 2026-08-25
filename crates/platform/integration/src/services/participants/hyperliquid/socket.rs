use std::task::{Context, Poll};

use crate::participants::hyperliquid::HyperliquidWebSocketConfig;
use crate::transport::websocket::{SocketEvent, TokioSocket};
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionKey, ConnectionLifecycle, ConnectionState,
    IntegrationError, ParticipantKind, ParticipantRef,
};

const IDLE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(90);

pub(crate) struct SocketService {
    state: ConnectionState,
    endpoint: String,
    event_capacity: usize,
    socket: Option<TokioSocket>,
}

pub(crate) struct RetiredSocket(Option<TokioSocket>);

impl SocketService {
    pub(crate) fn new(
        connection_key: ConnectionKey,
        config: HyperliquidWebSocketConfig,
    ) -> Result<Self, IntegrationError> {
        if !(config.endpoint.starts_with("ws://") || config.endpoint.starts_with("wss://")) {
            return Err(IntegrationError::InvalidRequest(
                "Hyperliquid WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        if config.event_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Hyperliquid WebSocket event capacity must be positive".into(),
            ));
        }
        let mut descriptor = ConnectionDescriptor::new(
            connection_key,
            ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid")
                .map_err(IntegrationError::InvalidRequest)?,
            "websocket",
        )
        .map_err(IntegrationError::InvalidRequest)?;
        descriptor.environment = config.environment;
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(descriptor),
            endpoint: config.endpoint,
            event_capacity: config.event_capacity,
            socket: None,
        })
    }

    pub(crate) async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Starting;
        match TokioSocket::connect(&self.endpoint, self.event_capacity).await {
            Ok(socket) => {
                self.socket = Some(socket);
                self.state.mark_ready(false);
                Ok(())
            },
            Err(error) => {
                self.state.mark_failed(error.clone());
                Err(IntegrationError::Transport(error))
            },
        }
    }

    pub(crate) async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.state.mark_stopped();
        Ok(())
    }

    pub(crate) async fn begin_replacement(&mut self) -> Result<RetiredSocket, IntegrationError> {
        let replacement = TokioSocket::connect(&self.endpoint, self.event_capacity)
            .await
            .map_err(IntegrationError::Transport)?;
        Ok(RetiredSocket(self.socket.replace(replacement)))
    }

    pub(crate) async fn commit_replacement(&mut self, mut retired: RetiredSocket) {
        if let Some(mut socket) = retired.0.take() {
            socket.close().await;
        }
        let authenticated = self.state.authenticated;
        self.state.mark_ready(authenticated);
        self.state.reconnect_count = self.state.reconnect_count.saturating_add(1);
    }

    pub(crate) async fn rollback_replacement(&mut self, mut retired: RetiredSocket) {
        if let Some(mut replacement) = self.socket.take() {
            replacement.close().await;
        }
        self.socket = retired.0.take();
        if self.socket.is_some() {
            let authenticated = self.state.authenticated;
            self.state.mark_ready(authenticated);
        }
    }

    pub(crate) fn health(&mut self) -> ConnectionHealth {
        self.state.health()
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        &self.state.identity
    }

    pub(crate) fn next_maintenance_at(&self) -> Option<tokio::time::Instant> {
        self.socket
            .as_ref()
            .map(|socket| socket.last_activity() + IDLE_TIMEOUT)
    }

    pub(crate) fn poll_maintenance(
        &self,
        now: tokio::time::Instant,
    ) -> Poll<Result<crate::MaintenanceOutcome, IntegrationError>> {
        match self.next_maintenance_at() {
            Some(deadline) if deadline <= now => {
                Poll::Ready(Ok(crate::MaintenanceOutcome::ReconnectRequired {
                    reason: "Hyperliquid WebSocket idle deadline elapsed".into(),
                }))
            },
            _ => Poll::Ready(Ok(crate::MaintenanceOutcome::Healthy)),
        }
    }

    pub(crate) async fn send(&mut self, payload: String) -> Result<(), IntegrationError> {
        self.socket
            .as_mut()
            .ok_or(IntegrationError::NotReady)?
            .send_text(payload)
            .await
            .map_err(IntegrationError::Transport)
    }

    pub(crate) async fn next(
        &mut self,
    ) -> Result<tokio_tungstenite::tungstenite::Message, IntegrationError> {
        loop {
            match self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .next_event()
                .await
            {
                SocketEvent::Message(tokio_tungstenite::tungstenite::Message::Ping(payload)) => {
                    self.socket
                        .as_mut()
                        .ok_or(IntegrationError::NotReady)?
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                },
                SocketEvent::Message(message) => return Ok(message),
                SocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
            }
        }
    }

    pub(crate) fn poll_next(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<Result<tokio_tungstenite::tungstenite::Message, IntegrationError>> {
        loop {
            let socket = match self.socket.as_mut() {
                Some(socket) => socket,
                None => return Poll::Ready(Err(IntegrationError::NotReady)),
            };
            match socket.poll_next_event(cx) {
                Poll::Ready(SocketEvent::Message(
                    tokio_tungstenite::tungstenite::Message::Ping(_),
                )) => continue,
                Poll::Ready(SocketEvent::Message(message)) => return Poll::Ready(Ok(message)),
                Poll::Ready(SocketEvent::Error(error)) => {
                    return Poll::Ready(Err(IntegrationError::Transport(error)));
                },
                Poll::Pending => return Poll::Pending,
            }
        }
    }
}
