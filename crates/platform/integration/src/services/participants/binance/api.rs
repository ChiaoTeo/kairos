use std::collections::BTreeMap;

use secrecy::ExposeSecret;
use serde_json::{Map, Value};
use tokio_tungstenite::tungstenite::Message;

use crate::participants::binance::{BinanceCredential, BinanceWebSocketConfig};
use crate::services::participants::binance::signing::sign_query;
use crate::services::participants::binance::socket::SocketService;
use crate::{ConnectionDescriptor, ConnectionHealth, IntegrationError, ParticipantRejection};

/// Reusable signed request/response protocol used by concrete Binance
/// WebSocket API connections. It is not a capability projection.
pub(crate) struct ApiService {
    socket: SocketService,
    credential: BinanceCredential,
    next_request_id: u64,
}

pub(crate) enum ApiReply {
    Confirmed(Value),
    Rejected(ParticipantRejection),
}

impl ApiReply {
    pub(crate) fn into_query_result(self) -> Result<Value, IntegrationError> {
        match self {
            Self::Confirmed(value) => Ok(value),
            Self::Rejected(rejection) => Err(IntegrationError::InvalidRequest(format!(
                "Binance rejected query{}: {}",
                rejection
                    .code
                    .as_deref()
                    .map(|code| format!(" ({code})"))
                    .unwrap_or_default(),
                rejection.message
            ))),
        }
    }
}

impl ApiService {
    pub(crate) fn new(
        connection_key: crate::ConnectionKey,
        config: BinanceWebSocketConfig,
        domain: &str,
    ) -> Result<Self, IntegrationError> {
        let credential = config.credential.clone().ok_or_else(|| {
            IntegrationError::Authentication("Binance WebSocket API credential is required".into())
        })?;
        let descriptor = config.descriptor(connection_key, domain)?;
        Ok(Self {
            socket: SocketService::new(descriptor, config.endpoint, config.event_capacity)?,
            credential,
            next_request_id: 1,
        })
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        self.socket.descriptor()
    }

    pub(crate) fn health(&mut self) -> ConnectionHealth {
        let mut health = self.socket.health();
        health.authenticated = health.healthy;
        health
    }

    pub(crate) async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.socket.connect().await
    }

    pub(crate) async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.socket.disconnect().await
    }

    pub(crate) async fn request(
        &mut self,
        method: &str,
        params: Vec<(&str, String)>,
    ) -> Result<ApiReply, IntegrationError> {
        let request_id = self.next_request_id;
        self.next_request_id = self.next_request_id.saturating_add(1);
        let mut signed = BTreeMap::from_iter(
            params
                .into_iter()
                .map(|(key, value)| (key.to_owned(), value)),
        );
        signed.insert(
            "apiKey".into(),
            self.credential.api_key.expose_secret().to_owned(),
        );
        signed.insert("timestamp".into(), now_millis().to_string());
        let payload = signed
            .iter()
            .map(|(key, value)| format!("{key}={value}"))
            .collect::<Vec<_>>()
            .join("&");
        signed.insert(
            "signature".into(),
            sign_query(self.credential.secret.expose_secret(), &payload)
                .map_err(|error| IntegrationError::Authentication(error.to_string()))?,
        );
        let params = Map::from_iter(
            signed
                .into_iter()
                .map(|(key, value)| (key.clone(), wire_value(&key, value))),
        );
        self.socket
            .send(
                serde_json::json!({
                    "id": request_id,
                    "method": method,
                    "params": params,
                })
                .to_string(),
            )
            .await?;
        loop {
            let message = self.socket.next().await?;
            let Message::Text(text) = message else {
                continue;
            };
            let value: Value = serde_json::from_str(&text)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
            if value.get("id").and_then(Value::as_u64) != Some(request_id) {
                continue;
            }
            let status = value.get("status").and_then(Value::as_u64).unwrap_or(200);
            if status >= 400 || value.get("error").is_some() {
                let error = value.get("error").unwrap_or(&value);
                return Ok(ApiReply::Rejected(ParticipantRejection {
                    code: error.get("code").map(|value| match value {
                        Value::String(code) => code.clone(),
                        other => other.to_string(),
                    }),
                    message: error
                        .get("msg")
                        .and_then(Value::as_str)
                        .unwrap_or("Binance WebSocket API rejected request")
                        .into(),
                    participant_request_id: Some(request_id.to_string()),
                }));
            }
            return Ok(ApiReply::Confirmed(
                value.get("result").cloned().unwrap_or(Value::Null),
            ));
        }
    }
}

fn wire_value(key: &str, value: String) -> Value {
    if matches!(key, "timestamp" | "recvWindow" | "orderId" | "limit") {
        if let Ok(value) = value.parse::<u64>() {
            return Value::Number(value.into());
        }
    }
    if matches!(key, "reduceOnly" | "closePosition") {
        if let Ok(value) = value.parse::<bool>() {
            return Value::Bool(value);
        }
    }
    Value::String(value)
}

fn now_millis() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};

    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| u64::try_from(value.as_millis()).unwrap_or(u64::MAX))
        .unwrap_or_default()
}
