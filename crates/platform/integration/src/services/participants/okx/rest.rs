use crate::participants::okx::{OkxCredential, OkxRestConfig};
use crate::services::participants::okx::signing::okx_signature;
use crate::transport::http::{ExchangeError, HttpClient};
use crate::{
    CommandOutcome, CommandResult, ConnectionDescriptor, IntegrationError, ParticipantKind,
    ParticipantRef,
};
use secrecy::ExposeSecret;

pub(crate) struct RestService {
    descriptor: ConnectionDescriptor,
    endpoint: String,
    client: HttpClient,
}

pub(crate) fn check_okx_response(
    value: serde_json::Value,
) -> Result<serde_json::Value, ExchangeError> {
    if value.get("code").and_then(serde_json::Value::as_str) != Some("0") {
        return Err(ExchangeError::Http {
            status: 200,
            body: value.to_string(),
        });
    }
    Ok(value)
}

impl RestService {
    pub(crate) fn new(
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
            config.binding_id,
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
        })
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub(crate) fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub(crate) fn client(&mut self) -> &mut HttpClient {
        &mut self.client
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
        let query_string = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(query.iter().map(|(key, value)| (*key, value.as_str())))
            .finish();
        let request_path = if query_string.is_empty() {
            path.to_owned()
        } else {
            format!("{path}?{query_string}")
        };
        let timestamp = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
        let signature = okx_signature(
            credential.secret.expose_secret(),
            &timestamp,
            "GET",
            &request_path,
            "",
        )
        .map_err(map_error)?;
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
            .map_err(map_error)
    }

    pub(crate) async fn private_post_command(
        &self,
        credential: &OkxCredential,
        path: &str,
        body: &serde_json::Value,
    ) -> CommandResult<serde_json::Value> {
        let timestamp = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
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
        match result {
            Ok(value) => Ok(CommandOutcome::Confirmed(value)),
            Err(error) => crate::transport::http::command_error_outcome(error),
        }
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
        ExchangeError::Http { status: 429, body } => IntegrationError::RateLimited(body),
        ExchangeError::Http { status, body } => {
            IntegrationError::Transport(format!("OKX HTTP {status}: {body}"))
        }
        other => IntegrationError::Transport(other.to_string()),
    }
}
