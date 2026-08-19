use std::future::Future;
use std::pin::Pin;

use secrecy::ExposeSecret;
use serde_json::Value;

use crate::participants::binance::BinanceCredential;
use crate::services::clock::{ServerClock, unix_millis};
use crate::services::participants::binance::signing::sign_query;
use crate::transport::http::{ExchangeError, HttpClient};
use crate::{CommandResult, ConnectionDescriptor, IntegrationError};

/// Reusable HTTP pool and endpoint metadata shared by Binance REST families.
/// The public lifecycle owner remains the concrete participant connection.
pub(crate) struct RestService {
    descriptor: ConnectionDescriptor,
    endpoint: String,
    client: HttpClient,
    credential: Option<BinanceCredential>,
    clock: ServerClock,
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
            clock: ServerClock::default(),
        })
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub(crate) fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub(crate) fn rate_limit_headers(&self) -> std::collections::BTreeMap<String, String> {
        self.client.diagnostics().last_rate_limit_headers
    }

    pub(crate) fn clock_health(&self) -> crate::ProviderClockHealth {
        self.clock.health()
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

    pub(crate) async fn signed_put_command(
        &mut self,
        path: &str,
        params: &[(&str, String)],
    ) -> CommandResult<Value> {
        self.signed_command(path, params, SignedMethod::Put).await
    }

    async fn signed_command(
        &mut self,
        path: &str,
        params: &[(&str, String)],
        method: SignedMethod,
    ) -> CommandResult<Value> {
        self.ensure_clock(path).await?;
        let result = self.signed_exchange_request(path, params, method).await;
        if is_timestamp_rejection(&result) {
            // The provider explicitly rejected this command, so resync for the
            // next caller attempt without transparently replaying it.
            self.sync_clock(path).await?;
        }
        match result {
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

    pub(crate) fn keep_alive_listen_key_future(
        &self,
        path: &'static str,
        listen_key: String,
    ) -> Result<Pin<Box<dyn Future<Output = Result<(), IntegrationError>> + Send>>, IntegrationError>
    {
        let api_key = self.credential()?.api_key.expose_secret().to_owned();
        let endpoint = format!("{}{}", self.endpoint, path);
        let client = self.client.clone();
        Ok(Box::pin(async move {
            let query = [("listenKey", listen_key)];
            let headers = [("X-MBX-APIKEY", api_key)];
            if path == "/sapi/v1/equity/listenKey" {
                client
                    .post_json_response_with_headers_and_query(&endpoint, &query, &headers)
                    .await
                    .map_err(map_error)?;
            } else {
                client
                    .put_query_json_response_with_headers_and_query(&endpoint, &query, &headers)
                    .await
                    .map_err(map_error)?;
            }
            Ok(())
        }))
    }

    async fn signed_request(
        &mut self,
        path: &str,
        params: &[(&str, String)],
        method: SignedMethod,
    ) -> Result<Value, IntegrationError> {
        self.ensure_clock(path).await?;
        let first = self.signed_exchange_request(path, params, method).await;
        if is_timestamp_rejection(&first) {
            self.sync_clock(path).await?;
            return self
                .signed_exchange_request(path, params, method)
                .await
                .map_err(map_error);
        }
        first.map_err(map_error)
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
        owned.push((
            "timestamp".into(),
            self.clock
                .adjusted_unix_millis()
                .map_err(|error| ExchangeError::InvalidRequest(error.to_string()))?
                .to_string(),
        ));
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
            },
            SignedMethod::Post => {
                self.client
                    .post_json_response_with_headers_and_query(&endpoint, &borrowed, &headers)
                    .await
            },
            SignedMethod::Put => {
                self.client
                    .put_command_json_response_with_headers_and_query(
                        &endpoint, &borrowed, &headers,
                    )
                    .await
            },
            SignedMethod::Delete => {
                self.client
                    .delete_json_response_with_headers_and_query(&endpoint, &borrowed, &headers)
                    .await
            },
        };
        response.map(|response| response.body)
    }

    async fn ensure_clock(&mut self, signed_path: &str) -> Result<(), IntegrationError> {
        if self.clock.is_fresh() {
            Ok(())
        } else {
            self.sync_clock(signed_path).await
        }
    }

    async fn sync_clock(&mut self, signed_path: &str) -> Result<(), IntegrationError> {
        let started = unix_millis()?;
        let response = self
            .client
            .get_json_response_with_headers_and_query(
                &format!("{}{}", self.endpoint, server_time_path(signed_path)),
                &[],
                &[],
            )
            .await
            .map_err(map_error)?;
        let received = unix_millis()?;
        let provider = response
            .body
            .get("serverTime")
            .and_then(Value::as_u64)
            .ok_or_else(|| {
                IntegrationError::InvalidPayload("Binance serverTime is missing".into())
            })?;
        self.clock.observe(provider, started, received)
    }
}

#[derive(Clone, Copy)]
enum SignedMethod {
    Get,
    Post,
    Put,
    Delete,
}

pub(crate) fn map_error(error: ExchangeError) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::Http {
            status: 429, body, ..
        } => IntegrationError::RateLimited(body),
        ExchangeError::Http { status, body, .. } => {
            IntegrationError::Transport(format!("Binance HTTP {status}: {body}"))
        },
        other => IntegrationError::Transport(other.to_string()),
    }
}

fn server_time_path(signed_path: &str) -> &'static str {
    if signed_path.starts_with("/fapi/") {
        "/fapi/v1/time"
    } else if signed_path.starts_with("/dapi/") {
        "/dapi/v1/time"
    } else if signed_path.starts_with("/eapi/") {
        "/eapi/v1/time"
    } else {
        "/api/v3/time"
    }
}

fn is_timestamp_rejection(result: &Result<Value, ExchangeError>) -> bool {
    matches!(
        result,
        Err(ExchangeError::Http { body, .. })
            if serde_json::from_str::<Value>(body)
                .ok()
                .and_then(|value| value.get("code").and_then(Value::as_i64))
                == Some(-1021)
    )
}

#[cfg(test)]
mod clock_tests {
    use super::*;

    #[test]
    fn server_time_endpoint_follows_binance_api_family() {
        assert_eq!(server_time_path("/api/v3/order"), "/api/v3/time");
        assert_eq!(server_time_path("/sapi/v1/order"), "/api/v3/time");
        assert_eq!(server_time_path("/fapi/v1/order"), "/fapi/v1/time");
        assert_eq!(server_time_path("/dapi/v1/order"), "/dapi/v1/time");
        assert_eq!(server_time_path("/eapi/v1/order"), "/eapi/v1/time");
    }

    #[test]
    fn recognizes_binance_timestamp_rejection_only() {
        let timestamp = Err(ExchangeError::Http {
            status: 400,
            body: r#"{"code":-1021,"msg":"outside recvWindow"}"#.into(),
            metadata: Default::default(),
        });
        assert!(is_timestamp_rejection(&timestamp));
    }
}
