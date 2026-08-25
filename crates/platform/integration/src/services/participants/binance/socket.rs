use std::task::{Context, Poll};

use crate::transport::websocket::{SocketEvent, TokioSocket};
use crate::{
    ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState, IntegrationError,
};

const IDLE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(90);
const SESSION_ROTATION_MARGIN: std::time::Duration = std::time::Duration::from_secs(5 * 60);

/// Reusable raw WebSocket mechanism owned by one concrete Binance connection.
pub(crate) struct SocketService {
    state: ConnectionState,
    endpoint: String,
    event_capacity: usize,
    socket: Option<TokioSocket>,
    established_at: Option<tokio::time::Instant>,
    session_lifetime: Option<std::time::Duration>,
    control_interval: Option<std::time::Duration>,
    next_control_at: Option<tokio::time::Instant>,
}

impl SocketService {
    pub(crate) fn new(
        descriptor: ConnectionDescriptor,
        endpoint: impl Into<String>,
        event_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let endpoint = endpoint.into();
        if !(endpoint.starts_with("ws://") || endpoint.starts_with("wss://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        if event_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Binance WebSocket event capacity must be positive".into(),
            ));
        }
        Ok(Self {
            state: ConnectionState::new(descriptor),
            endpoint,
            event_capacity,
            socket: None,
            established_at: None,
            session_lifetime: None,
            control_interval: None,
            next_control_at: None,
        })
    }

    pub(crate) fn with_market_policy(
        mut self,
        max_incoming_messages_per_second: u32,
        session_lifetime: std::time::Duration,
    ) -> Result<Self, IntegrationError> {
        // Reserve one message/second for ping/pong traffic instead of allowing
        // application control messages to consume the provider's full quota.
        let application_rate = max_incoming_messages_per_second.saturating_sub(1);
        if application_rate == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Binance WebSocket message policy must leave control headroom".into(),
            ));
        }
        if session_lifetime <= SESSION_ROTATION_MARGIN {
            return Err(IntegrationError::InvalidRequest(
                "Binance WebSocket session lifetime must exceed the rotation margin".into(),
            ));
        }
        self.control_interval = Some(std::time::Duration::from_secs_f64(
            1.0 / f64::from(application_rate),
        ));
        self.session_lifetime = Some(session_lifetime);
        Ok(self)
    }

    pub(crate) async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Starting;
        match TokioSocket::connect(&self.endpoint, self.event_capacity).await {
            Ok(socket) => {
                self.socket = Some(socket);
                self.established_at = Some(tokio::time::Instant::now());
                self.next_control_at = None;
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
        self.state.lifecycle = ConnectionLifecycle::Stopping;
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.state.mark_stopped();
        self.established_at = None;
        self.next_control_at = None;
        Ok(())
    }

    pub(crate) fn health(&mut self) -> ConnectionHealth {
        self.state.health()
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        &self.state.identity
    }

    #[cfg(test)]
    pub(crate) fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub(crate) fn next_maintenance_at(&self) -> Option<tokio::time::Instant> {
        let idle = self
            .socket
            .as_ref()
            .map(|socket| socket.last_activity() + IDLE_TIMEOUT);
        let rotation = self.established_at.zip(self.session_lifetime).map(
            |(established_at, session_lifetime)| {
                established_at + session_lifetime - SESSION_ROTATION_MARGIN
            },
        );
        idle.into_iter().chain(rotation).min()
    }

    pub(crate) fn poll_maintenance(
        &self,
        now: tokio::time::Instant,
    ) -> Poll<Result<crate::MaintenanceOutcome, IntegrationError>> {
        let rotation = self.established_at.zip(self.session_lifetime).map(
            |(established_at, session_lifetime)| {
                established_at + session_lifetime - SESSION_ROTATION_MARGIN
            },
        );
        if rotation.is_some_and(|deadline| deadline <= now) {
            return Poll::Ready(Ok(crate::MaintenanceOutcome::ReconnectRequired {
                reason: "Binance WebSocket session rotation deadline reached".into(),
            }));
        }
        match self
            .socket
            .as_ref()
            .map(|socket| socket.last_activity() + IDLE_TIMEOUT)
        {
            Some(deadline) if deadline <= now => {
                Poll::Ready(Ok(crate::MaintenanceOutcome::ReconnectRequired {
                    reason: "Binance WebSocket idle deadline elapsed".into(),
                }))
            },
            _ => Poll::Ready(Ok(crate::MaintenanceOutcome::Healthy)),
        }
    }

    pub(crate) async fn send(&mut self, payload: String) -> Result<(), IntegrationError> {
        if let Some(deadline) = self.next_control_at {
            tokio::time::sleep_until(deadline).await;
        }
        self.socket
            .as_mut()
            .ok_or(IntegrationError::NotReady)?
            .send_text(payload)
            .await
            .map_err(IntegrationError::Transport)?;
        self.next_control_at = self
            .control_interval
            .map(|interval| tokio::time::Instant::now() + interval);
        Ok(())
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

#[cfg(test)]
mod tests {
    use crate::domain::{ConnectionDescriptor, ParticipantKind, ParticipantRef};

    use super::*;

    fn descriptor() -> ConnectionDescriptor {
        ConnectionDescriptor::new(
            "binance-market-test",
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            "market.websocket",
        )
        .unwrap()
    }

    #[test]
    fn market_policy_reserves_message_headroom() {
        let spot = SocketService::new(descriptor(), "wss://example.test/ws", 1)
            .unwrap()
            .with_market_policy(5, std::time::Duration::from_secs(86_400))
            .unwrap();
        let futures = SocketService::new(descriptor(), "wss://example.test/ws", 1)
            .unwrap()
            .with_market_policy(10, std::time::Duration::from_secs(86_400))
            .unwrap();

        assert_eq!(
            spot.control_interval,
            Some(std::time::Duration::from_millis(250))
        );
        assert!(futures.control_interval.unwrap() > std::time::Duration::from_millis(111));
        assert!(futures.control_interval.unwrap() < std::time::Duration::from_millis(112));
    }

    #[tokio::test]
    async fn session_rotation_is_requested_before_provider_disconnect() {
        let mut service = SocketService::new(descriptor(), "wss://example.test/ws", 1)
            .unwrap()
            .with_market_policy(10, std::time::Duration::from_secs(86_400))
            .unwrap();
        let now = tokio::time::Instant::now();
        service.established_at = Some(now - std::time::Duration::from_secs(86_400));

        assert!(matches!(
            service.poll_maintenance(now),
            Poll::Ready(Ok(crate::MaintenanceOutcome::ReconnectRequired { reason }))
                if reason == "Binance WebSocket session rotation deadline reached"
        ));
    }
}
