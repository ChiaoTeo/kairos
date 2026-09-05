use secrecy::ExposeSecret;

use crate::participants::okx::{OkxCredential, OkxRestConfig};
use crate::services::clock::{ServerClock, unix_millis};
use crate::services::participants::okx::signing::okx_signature;
use crate::transport::http::{ExchangeError, HttpClient};
use crate::{
    CommandOutcome, CommandResult, ConnectionDescriptor, ConnectionKey, IntegrationError,
    ParticipantKind, ParticipantRef,
};

pub(crate) struct RestService {
    descriptor: ConnectionDescriptor,
    endpoint: String,
    client: HttpClient,
    clock: std::sync::Mutex<ServerClock>,
}

pub(crate) fn check_okx_response(
    value: serde_json::Value,
) -> Result<serde_json::Value, ExchangeError> {
    if value.get("code").and_then(serde_json::Value::as_str) != Some("0") {
        return Err(ExchangeError::Http {
            status: 200,
            body: value.to_string(),
            metadata: Default::default(),
        });
    }
    Ok(value)
}

impl RestService {
    pub(crate) fn new(
        connection_key: ConnectionKey,
        config: OkxRestConfig,
        domain: &str,
        principal_id: Option<String>,
    ) -> Result<Self, IntegrationError> {
        let endpoint = config.endpoint.trim_end_matches('/').to_owned();
        if !(endpoint.starts_with("http://") || endpoint.starts_with("https://")) {
            return Err(IntegrationError::InvalidRequest(
                "OKX REST endpoint must start with http:// or https://".into(),
            ));
        }
        let mut descriptor = ConnectionDescriptor::new(
            connection_key,
            ParticipantRef::new(ParticipantKind::Exchange, "okx")
                .map_err(IntegrationError::InvalidRequest)?,
            domain,
        )
        .map_err(IntegrationError::InvalidRequest)?;
        descriptor.environment = config.environment;
        descriptor.principal_id = principal_id;
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            descriptor,
            endpoint,
            client: HttpClient::new("kairos-integration/okx")
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
            clock: std::sync::Mutex::new(ServerClock::default()),
        })
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub(crate) fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub(crate) fn client(&self) -> &HttpClient {
        &self.client
    }

    pub(crate) fn rate_limit_headers(&self) -> std::collections::BTreeMap<String, String> {
        self.client.diagnostics().last_rate_limit_headers
    }

    pub(crate) fn clock_health(&self) -> Result<crate::ProviderClockHealth, IntegrationError> {
        self.clock
            .lock()
            .map(|clock| clock.health())
            .map_err(|_| IntegrationError::Unavailable("OKX clock lock is poisoned".into()))
    }

    pub(crate) async fn public_get(
        &self,
        path: &str,
        query: &[(&str, String)],
    ) -> Result<serde_json::Value, IntegrationError> {
        self.client
            .get_json_response_with_headers_and_query(
                &format!("{}{}", self.endpoint, path),
                query,
                &[],
            )
            .await
            .map(|response| response.body)
            .and_then(check_okx_response)
            .map_err(map_error)
    }

    pub(crate) async fn private_get(
        &self,
        credential: &OkxCredential,
        path: &str,
        query: &[(&str, String)],
    ) -> Result<serde_json::Value, IntegrationError> {
        self.ensure_clock().await?;
        let first = self.private_get_once(credential, path, query).await;
        if is_timestamp_rejection(&first) {
            self.sync_clock().await?;
            return self
                .private_get_once(credential, path, query)
                .await
                .map_err(map_error);
        }
        first.map_err(map_error)
    }

    async fn private_get_once(
        &self,
        credential: &OkxCredential,
        path: &str,
        query: &[(&str, String)],
    ) -> Result<serde_json::Value, ExchangeError> {
        let query_string = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(query.iter().map(|(key, value)| (*key, value.as_str())))
            .finish();
        let request_path = if query_string.is_empty() {
            path.to_owned()
        } else {
            format!("{path}?{query_string}")
        };
        let timestamp = self
            .adjusted_timestamp()
            .map_err(|error| ExchangeError::InvalidRequest(error.to_string()))?;
        let signature = okx_signature(
            credential.secret.expose_secret(),
            &timestamp,
            "GET",
            &request_path,
            "",
        )?;
        let headers = private_headers(credential, signature, timestamp);
        self.client
            .get_json_response_with_headers_and_query(
                &format!("{}{}", self.endpoint, path),
                query,
                &headers,
            )
            .await
            .map(|response| response.body)
            .and_then(check_okx_response)
    }

    pub(crate) async fn private_post_command(
        &self,
        credential: &OkxCredential,
        path: &str,
        body: &serde_json::Value,
    ) -> CommandResult<serde_json::Value> {
        self.ensure_clock().await?;
        let timestamp = self.adjusted_timestamp()?;
        let signature = okx_signature(
            credential.secret.expose_secret(),
            &timestamp,
            "POST",
            path,
            &body.to_string(),
        )
        .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        let mut headers = private_headers(credential, signature, timestamp).to_vec();
        headers.push(("Content-Type", "application/json".into()));
        let result = self
            .client
            .post_json_command_with_headers(&format!("{}{}", self.endpoint, path), &headers, body)
            .await
            .map(|response| response.body)
            .and_then(check_okx_response);
        if is_timestamp_rejection(&result) {
            // The command was explicitly rejected. Refresh only the clock;
            // the caller decides whether to submit a new command.
            self.sync_clock().await?;
        }
        match result {
            Ok(value) => Ok(CommandOutcome::Confirmed(value)),
            Err(error) => crate::transport::http::command_error_outcome(error),
        }
    }

    async fn ensure_clock(&self) -> Result<(), IntegrationError> {
        let fresh = self
            .clock
            .lock()
            .map_err(|_| IntegrationError::Unavailable("OKX clock lock is poisoned".into()))?
            .is_fresh();
        if fresh {
            Ok(())
        } else {
            self.sync_clock().await
        }
    }

    async fn sync_clock(&self) -> Result<(), IntegrationError> {
        let started = unix_millis()?;
        let response = self
            .client
            .get_json_response_with_headers_and_query(
                &format!("{}/api/v5/public/time", self.endpoint),
                &[],
                &[],
            )
            .await
            .map_err(map_error)?;
        let received = unix_millis()?;
        let provider = response
            .body
            .pointer("/data/0/ts")
            .and_then(serde_json::Value::as_str)
            .and_then(|value| value.parse::<u64>().ok())
            .ok_or_else(|| IntegrationError::InvalidPayload("OKX server time is missing".into()))?;
        self.clock
            .lock()
            .map_err(|_| IntegrationError::Unavailable("OKX clock lock is poisoned".into()))?
            .observe(provider, started, received)
    }

    fn adjusted_timestamp(&self) -> Result<String, IntegrationError> {
        let millis = self
            .clock
            .lock()
            .map_err(|_| IntegrationError::Unavailable("OKX clock lock is poisoned".into()))?
            .adjusted_unix_millis()?;
        let millis = i64::try_from(millis).map_err(|_| {
            IntegrationError::Unavailable("OKX timestamp cannot be represented".into())
        })?;
        chrono::DateTime::from_timestamp_millis(millis)
            .map(|value| value.to_rfc3339_opts(chrono::SecondsFormat::Millis, true))
            .ok_or_else(|| IntegrationError::Unavailable("OKX timestamp is out of range".into()))
    }
}

fn private_headers(
    credential: &OkxCredential,
    signature: String,
    timestamp: String,
) -> [(&'static str, String); 4] {
    use secrecy::ExposeSecret;

    [
        ("OK-ACCESS-KEY", credential.api_key.expose_secret().into()),
        ("OK-ACCESS-SIGN", signature),
        ("OK-ACCESS-TIMESTAMP", timestamp),
        (
            "OK-ACCESS-PASSPHRASE",
            credential.passphrase.expose_secret().into(),
        ),
    ]
}

pub(crate) fn map_error(error: ExchangeError) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::Http {
            status: 429, body, ..
        } => IntegrationError::RateLimited(body),
        ExchangeError::Http { status, body, .. } => {
            IntegrationError::Transport(format!("OKX HTTP {status}: {body}"))
        },
        other => IntegrationError::Transport(other.to_string()),
    }
}

fn is_timestamp_rejection(result: &Result<serde_json::Value, ExchangeError>) -> bool {
    matches!(
        result,
        Err(ExchangeError::Http { body, .. })
            if serde_json::from_str::<serde_json::Value>(body)
                .ok()
                .and_then(|value| value.get("code").and_then(serde_json::Value::as_str).map(str::to_owned))
                .as_deref()
                == Some("50102")
    )
}

#[cfg(test)]
mod clock_tests {
    use super::*;

    #[test]
    fn recognizes_okx_timestamp_rejection_only() {
        let timestamp = Err(ExchangeError::Http {
            status: 200,
            body: r#"{"code":"50102","msg":"Timestamp request expired"}"#.into(),
            metadata: Default::default(),
        });
        assert!(is_timestamp_rejection(&timestamp));
    }
}
