use crate::participants::binance::BinanceCredential;
use crate::services::participants::binance::signing::sign_query;
use crate::transport::http::ExchangeError;
use crate::transport::http::HttpClient;
use crate::{CommandResult, ConnectionDescriptor, IntegrationError};
use secrecy::ExposeSecret;
use serde_json::Value;
use std::time::{SystemTime, UNIX_EPOCH};

/// Reusable HTTP pool and endpoint metadata shared by Binance REST families.
/// The public lifecycle owner remains the concrete participant connection.
pub(crate) struct RestService {
    descriptor: ConnectionDescriptor,
    endpoint: String,
    client: HttpClient,
    credential: Option<BinanceCredential>,
}

impl RestService {
    pub(crate) fn new(
        descriptor: ConnectionDescriptor,
        endpoint: impl Into<String>,
        credential: Option<BinanceCredential>,
    ) -> Result<Self, IntegrationError> {
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let endpoint = endpoint.into().trim_end_matches('/').to_owned();
        if !(endpoint.starts_with("http://") || endpoint.starts_with("https://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance REST endpoint must start with http:// or https://".into(),
            ));
        }
        Ok(Self {
            descriptor,
            endpoint,
            client: HttpClient::new("kairos-integration/binance")
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
            credential,
        })
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub(crate) fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub(crate) fn credential(&self) -> Result<&BinanceCredential, IntegrationError> {
        self.credential.as_ref().ok_or_else(|| {
            IntegrationError::Authentication("Binance credential is required".into())
        })
    }

    pub(crate) async fn public_get(
        &mut self,
        path: &str,
        params: &[(&str, String)],
    ) -> Result<Value, IntegrationError> {
        let endpoint = format!("{}{}", self.endpoint, path);
        self.client
            .get_json_response_with_headers_and_query(&endpoint, params, &[])
            .await
            .map(|response| response.body)
            .map_err(map_error)
    }

    /// API-key authenticated query which does not require a signature.
    pub(crate) async fn keyed_get(
        &mut self,
        path: &str,
        params: &[(&str, String)],
    ) -> Result<Value, IntegrationError> {
        let api_key = self.credential()?.api_key.expose_secret().to_owned();
        self.client
            .get_json_response_with_headers_and_query(
                &format!("{}{}", self.endpoint, path),
                params,
                &[("X-MBX-APIKEY", api_key)],
            )
            .await
            .map(|response| response.body)
            .map_err(map_error)
    }

    pub(crate) async fn signed_get(
        &mut self,
        path: &str,
        params: &[(&str, String)],
    ) -> Result<Value, IntegrationError> {
        self.signed_request(path, params, SignedMethod::Get).await
    }

    /// Signed query transported with HTTP POST. Some Binance read APIs use
    /// POST despite having query semantics.
    pub(crate) async fn signed_post_query(
        &mut self,
        path: &str,
        params: &[(&str, String)],
    ) -> Result<Value, IntegrationError> {
        self.signed_request(path, params, SignedMethod::Post).await
    }

    pub(crate) async fn signed_post_command(
        &mut self,
        path: &str,
        params: &[(&str, String)],
    ) -> CommandResult<Value> {
        self.signed_command(path, params, SignedMethod::Post).await
    }

    pub(crate) async fn signed_delete_command(
        &mut self,
        path: &str,
        params: &[(&str, String)],
    ) -> CommandResult<Value> {
        self.signed_command(path, params, SignedMethod::Delete)
            .await
    }

    async fn signed_command(
        &mut self,
        path: &str,
        params: &[(&str, String)],
        method: SignedMethod,
    ) -> CommandResult<Value> {
        match self.signed_exchange_request(path, params, method).await {
            Ok(value) => Ok(crate::CommandOutcome::Confirmed(value)),
            Err(error) => crate::transport::http::command_error_outcome(error),
        }
    }

    pub(crate) async fn create_listen_key(&self, path: &str) -> Result<String, IntegrationError> {
        let credential = self.credential()?;
        let response = self
            .client
            .post_json_response_with_headers_and_query(
                &format!("{}{}", self.endpoint, path),
                &[],
                &[("X-MBX-APIKEY", credential.api_key.expose_secret().into())],
            )
            .await
            .map_err(map_error)?;
        response
            .body
            .get("listenKey")
            .or_else(|| response.body.pointer("/data/listenKey"))
            .and_then(serde_json::Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .ok_or_else(|| IntegrationError::InvalidPayload("Binance listenKey is missing".into()))
    }

    pub(crate) async fn keep_alive_listen_key(
        &self,
        path: &str,
        listen_key: &str,
    ) -> Result<(), IntegrationError> {
        let credential = self.credential()?;
        let endpoint = format!("{}{}", self.endpoint, path);
        let query = [("listenKey", listen_key.to_owned())];
        let headers = [("X-MBX-APIKEY", credential.api_key.expose_secret().into())];
        if path == "/sapi/v1/equity/listenKey" {
            self.client
                .post_json_response_with_headers_and_query(&endpoint, &query, &headers)
                .await
                .map_err(map_error)?;
        } else {
            self.client
                .put_query_json_response_with_headers_and_query(&endpoint, &query, &headers)
                .await
                .map_err(map_error)?;
        }
        Ok(())
    }

    async fn signed_request(
        &mut self,
        path: &str,
        params: &[(&str, String)],
        method: SignedMethod,
    ) -> Result<Value, IntegrationError> {
        self.signed_exchange_request(path, params, method)
            .await
            .map_err(map_error)
    }

    async fn signed_exchange_request(
        &mut self,
        path: &str,
        params: &[(&str, String)],
        method: SignedMethod,
    ) -> Result<Value, ExchangeError> {
        let credential = self
            .credential()
            .map_err(|error| ExchangeError::Authentication(error.to_string()))?
            .clone();
        let mut owned = params
            .iter()
            .map(|(key, value)| ((*key).to_owned(), value.clone()))
            .collect::<Vec<_>>();
        owned.push(("timestamp".into(), now_millis().to_string()));
        owned.sort_by(|left, right| left.0.cmp(&right.0));
        let query = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(
                owned
                    .iter()
                    .map(|(key, value)| (key.as_str(), value.as_str())),
            )
            .finish();
        owned.push((
            "signature".into(),
            sign_query(credential.secret.expose_secret(), &query)?,
        ));
        let borrowed = owned
            .iter()
            .map(|(key, value)| (key.as_str(), value.clone()))
            .collect::<Vec<_>>();
        let headers = [(
            "X-MBX-APIKEY",
            credential.api_key.expose_secret().to_owned(),
        )];
        let endpoint = format!("{}{}", self.endpoint, path);
        let response = match method {
            SignedMethod::Get => {
                self.client
                    .get_json_response_with_headers_and_query(&endpoint, &borrowed, &headers)
                    .await
            }
            SignedMethod::Post => {
                self.client
                    .post_json_response_with_headers_and_query(&endpoint, &borrowed, &headers)
                    .await
            }
            SignedMethod::Delete => {
                self.client
                    .delete_json_response_with_headers_and_query(&endpoint, &borrowed, &headers)
                    .await
            }
        };
        response.map(|response| response.body)
    }
}

#[derive(Clone, Copy)]
enum SignedMethod {
    Get,
    Post,
    Delete,
}

pub(crate) fn map_error(error: ExchangeError) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::Http { status: 429, body } => IntegrationError::RateLimited(body),
        ExchangeError::Http { status, body } => {
            IntegrationError::Transport(format!("Binance HTTP {status}: {body}"))
        }
        other => IntegrationError::Transport(other.to_string()),
    }
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_millis())
        .ok()
        .and_then(|value| u64::try_from(value).ok())
        .unwrap_or_default()
}
