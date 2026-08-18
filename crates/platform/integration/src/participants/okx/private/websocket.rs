use std::collections::VecDeque;
use std::time::{SystemTime, UNIX_EPOCH};

use secrecy::ExposeSecret;
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

use crate::participants::okx::{OkxCredential, OkxPrivateWebSocketConfig};
use crate::services::participants::okx::{
    signing::okx_signature,
    socket::SocketService,
    stream::{parse_event, parse_execution_events},
};
use crate::{
    AccountStream, ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery,
    ConnectionLifecycleCommand, ExecutionStream, ExternalAccountEventEnvelope,
    ExternalEventEnvelope, ExternalExecutionEvent, ExternalParticipantEvent, IntegrationError,
    ParticipantEventStream,
};

/// One authenticated `/ws/v5/private` socket shared by account and execution streams.
pub struct OkxPrivateWebSocketConnection {
    service: SocketService,
    credential: OkxCredential,
    segment_key: String,
    trading_mode: String,
    channel_epoch: u64,
    pending_accounts: VecDeque<ExternalAccountEventEnvelope>,
    pending_executions: VecDeque<ExternalEventEnvelope<ExternalExecutionEvent>>,
    event_capacity: usize,
}

impl OkxPrivateWebSocketConnection {
    pub fn new(config: OkxPrivateWebSocketConfig) -> Result<Self, IntegrationError> {
        if config.segment_key.trim().is_empty() || config.trading_mode.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "OKX private stream segment key and trading mode are required".into(),
            ));
        }
        let principal_id = config.credential.principal_id.clone();
        let event_capacity = config.connection.event_capacity;
        Ok(Self {
            service: SocketService::new(
                config.connection,
                "private.websocket",
                Some(principal_id),
            )?,
            credential: config.credential,
            segment_key: config.segment_key,
            trading_mode: config.trading_mode,
            channel_epoch: 0,
            pending_accounts: VecDeque::new(),
            pending_executions: VecDeque::new(),
            event_capacity,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    async fn login(&mut self) -> Result<(), IntegrationError> {
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|error| IntegrationError::Unavailable(error.to_string()))?
            .as_secs()
            .to_string();
        let signature = okx_signature(
            self.credential.secret.expose_secret(),
            &timestamp,
            "GET",
            "/users/self/verify",
            "",
        )
        .map_err(|error| IntegrationError::Authentication(error.to_string()))?;
        self.service
            .send(
                json!({
                    "op": "login",
                    "args": [{
                        "apiKey": self.credential.api_key.expose_secret(),
                        "passphrase": self.credential.passphrase.expose_secret(),
                        "timestamp": timestamp,
                        "sign": signature
                    }]
                })
                .to_string(),
            )
            .await?;
        loop {
            let value = self.next_value().await?;
            if value.get("event").and_then(Value::as_str) == Some("login") {
                if value.get("code").and_then(Value::as_str).unwrap_or("0") == "0" {
                    self.service.mark_authenticated();
                    return Ok(());
                }
                return Err(IntegrationError::Authentication(
                    value
                        .get("msg")
                        .and_then(Value::as_str)
                        .unwrap_or("OKX login rejected")
                        .into(),
                ));
            }
            self.demultiplex(&value)?;
        }
    }

    async fn subscribe_channels(&mut self) -> Result<(), IntegrationError> {
        let request_id = format!("private-{}", self.channel_epoch);
        self.service
            .send(
                json!({
                    "id": request_id,
                    "op": "subscribe",
                    "args": [
                        {"channel": "account"},
                        {"channel": "positions", "instType": "ANY"},
                        {"channel": "balance_and_position"},
                        {"channel": "orders", "instType": "ANY"}
                    ]
                })
                .to_string(),
            )
            .await?;
        loop {
            let value = self.next_value().await?;
            if value.get("id").and_then(Value::as_str) == Some(request_id.as_str()) {
                if value.get("event").and_then(Value::as_str) == Some("error")
                    || value
                        .get("code")
                        .and_then(Value::as_str)
                        .is_some_and(|code| code != "0")
                {
                    return Err(IntegrationError::InvalidRequest(
                        value
                            .get("msg")
                            .and_then(Value::as_str)
                            .unwrap_or("OKX private subscription rejected")
                            .into(),
                    ));
                }
                return Ok(());
            }
            self.demultiplex(&value)?;
        }
    }

    async fn next_value(&mut self) -> Result<Value, IntegrationError> {
        loop {
            match self.service.next().await? {
                Message::Text(text) if text.as_str() == "pong" => continue,
                Message::Text(text) => {
                    return serde_json::from_str(&text)
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
                }
                Message::Close(_) => {
                    return Err(IntegrationError::Transport(
                        "OKX private WebSocket closed".into(),
                    ))
                }
                _ => continue,
            }
        }
    }

    fn demultiplex(&mut self, value: &Value) -> Result<(), IntegrationError> {
        if value.get("event").and_then(Value::as_str) == Some("error") {
            return Err(IntegrationError::InvalidPayload(
                value
                    .get("msg")
                    .and_then(Value::as_str)
                    .unwrap_or("OKX stream error")
                    .into(),
            ));
        }
        let text = value.to_string();
        let received = now_nanos();
        let descriptor = self.descriptor().clone();
        let channel_id = format!("{}.private", descriptor.binding_id);
        let account = parse_event(&self.segment_key, &text)
            .map_err(IntegrationError::InvalidPayload)?
            .map(|payload| ExternalEventEnvelope {
                participant: descriptor.participant.clone(),
                binding_id: descriptor.binding_id.clone(),
                channel_id: channel_id.clone(),
                channel_epoch: self.channel_epoch,
                participant_event_id: None,
                participant_sequence: value.get("seqId").and_then(Value::as_u64),
                observed_at_unix_nanos: received,
                received_at_unix_nanos: received,
                payload,
            });
        let executions = parse_execution_events(
            &descriptor.binding_id,
            &channel_id,
            self.channel_epoch,
            &self.trading_mode,
            received,
            &text,
        )
        .map_err(IntegrationError::InvalidPayload)?;
        if self.pending_accounts.len()
            + self.pending_executions.len()
            + usize::from(account.is_some())
            + executions.len()
            > self.event_capacity
        {
            return Err(IntegrationError::Backpressure(
                "OKX private event buffer overflowed".into(),
            ));
        }
        self.pending_accounts.extend(account);
        self.pending_executions.extend(executions);
        Ok(())
    }

    async fn receive(&mut self) -> Result<(), IntegrationError> {
        let value = self.next_value().await?;
        self.demultiplex(&value)
    }
}

impl ConnectionHealthQuery for OkxPrivateWebSocketConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        self.service.health()
    }
}

impl ConnectionLifecycleCommand for OkxPrivateWebSocketConnection {
    async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.service.connect().await?;
        self.channel_epoch = self.channel_epoch.saturating_add(1);
        self.login().await?;
        self.subscribe_channels().await
    }

    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.pending_accounts.clear();
        self.pending_executions.clear();
        self.service.disconnect().await
    }

    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.disconnect().await?;
        self.connect().await
    }
}

impl AccountStream for OkxPrivateWebSocketConnection {
    async fn next(&mut self) -> Result<ExternalAccountEventEnvelope, IntegrationError> {
        loop {
            if let Some(event) = self.pending_accounts.pop_front() {
                return Ok(event);
            }
            self.receive().await?;
        }
    }
}

impl ExecutionStream for OkxPrivateWebSocketConnection {
    async fn next(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        loop {
            if let Some(event) = self.pending_executions.pop_front() {
                return Ok(event);
            }
            self.receive().await?;
        }
    }
}

impl ParticipantEventStream for OkxPrivateWebSocketConnection {
    async fn next(&mut self) -> Result<ExternalParticipantEvent, IntegrationError> {
        loop {
            if let Some(event) = self.pending_accounts.pop_front() {
                return Ok(ExternalParticipantEvent::Account(event));
            }
            if let Some(event) = self.pending_executions.pop_front() {
                return Ok(ExternalParticipantEvent::Execution(event));
            }
            self.receive().await?;
        }
    }
}

fn now_nanos() -> kairos_primitives::UnixNanos {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    kairos_primitives::UnixNanos::from(u64::try_from(nanos).unwrap_or(u64::MAX))
}
