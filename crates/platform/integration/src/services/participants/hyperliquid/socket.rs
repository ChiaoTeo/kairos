use crate::participants::hyperliquid::HyperliquidWebSocketConfig;
use crate::transport::websocket::{SocketEvent, TokioSocket};
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState, IntegrationError,
    ParticipantKind, ParticipantRef,
};

pub(crate) struct SocketService {
    state: ConnectionState,
    endpoint: String,
    event_capacity: usize,
    socket: Option<TokioSocket>,
}

impl SocketService {
    pub(crate) fn new(config: HyperliquidWebSocketConfig) -> Result<Self, IntegrationError> {
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
            config.binding_id,
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
            }
            Err(error) => {
                self.state.mark_failed(error.clone());
                Err(IntegrationError::Transport(error))
            }
        }
    }

    pub(crate) async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.state.mark_stopped();
        Ok(())
    }

    pub(crate) fn health(&mut self) -> ConnectionHealth {
        self.state.health()
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        &self.state.identity
    }

    pub(crate) async fn send(&self, payload: String) -> Result<(), IntegrationError> {
        self.socket
            .as_ref()
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
                        .as_ref()
                        .ok_or(IntegrationError::NotReady)?
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                }
                SocketEvent::Message(message) => return Ok(message),
                SocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
                SocketEvent::Backpressure => {
                    return Err(IntegrationError::Backpressure(
                        "Hyperliquid WebSocket event queue overflowed".into(),
                    ))
                }
            }
        }
    }
}
