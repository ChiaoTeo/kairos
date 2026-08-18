//! Provider-neutral HTTP foundation for exchange adapters.
//!
//! This crate deliberately knows nothing about markets, accounts, or
//! FlatBuffers. Exchange-specific crates convert its JSON result into their
//! own provider records.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use reqwest::Method;
use serde_json::Value;
use thiserror::Error;

use crate::{CommandOutcome, IndeterminateCommand, IntegrationError, ParticipantRejection};

#[derive(Debug, Error)]
pub enum ExchangeError {
    #[error("HTTP request failed: {0}")]
    Transport(#[from] reqwest::Error),
    #[error("exchange returned HTTP status {status}: {body}")]
    Http {
        status: u16,
        body: String,
        metadata: HttpResponseMetadata,
    },
    #[error("invalid exchange response: {0}")]
    Response(#[from] serde_json::Error),
    #[error("exchange returned invalid JSON: {message}; body: {body}")]
    InvalidJson { message: String, body: String },
    #[error("exchange authentication failed: {0}")]
    Authentication(String),
    #[error("invalid exchange request: {0}")]
    InvalidRequest(String),
}

/// Convert an error observed after starting a provider command into a safe
/// application outcome. Explicit HTTP/provider responses are rejections;
/// transport, server, and response-decoding failures are conservatively
/// indeterminate because the provider may already have applied the command.
pub(crate) fn command_error_outcome<T>(
    error: ExchangeError,
) -> Result<CommandOutcome<T>, IntegrationError> {
    match error {
        ExchangeError::Authentication(message) => Err(IntegrationError::Authentication(message)),
        ExchangeError::InvalidRequest(message) => Err(IntegrationError::InvalidRequest(message)),
        ExchangeError::Http {
            status: 401, body, ..
        } => Err(IntegrationError::Authentication(diagnostic_body(&body))),
        ExchangeError::Http {
            status: 403, body, ..
        } => Err(IntegrationError::Authorization(diagnostic_body(&body))),
        ExchangeError::Http { status, body, .. }
            if status == 200
                || (400..500).contains(&status) && !matches!(status, 408 | 409 | 425 | 429) =>
        {
            Ok(CommandOutcome::Rejected(provider_rejection(status, &body)))
        }
        other => Ok(CommandOutcome::Indeterminate(
            IndeterminateCommand::may_have_been_sent(other.to_string()),
        )),
    }
}

fn provider_rejection(status: u16, body: &str) -> ParticipantRejection {
    let payload = serde_json::from_str::<Value>(body).ok();
    let code = payload
        .as_ref()
        .and_then(|value| value.get("code"))
        .map(|value| match value {
            Value::String(code) => code.clone(),
            other => other.to_string(),
        })
        .or_else(|| Some(status.to_string()));
    let message = payload
        .as_ref()
        .and_then(|value| value.get("msg").or_else(|| value.get("message")))
        .and_then(Value::as_str)
        .map(diagnostic_body)
        .unwrap_or_else(|| diagnostic_body(body));
    ParticipantRejection {
        code,
        message,
        participant_request_id: None,
    }
}

#[derive(Clone, Debug)]
pub struct HttpJsonResponse {
    pub body: Value,
    pub metadata: HttpResponseMetadata,
}

/// Sanitized provider response evidence. Only rate-limit and timing headers
/// are retained; authentication and cookie headers are never copied.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct HttpResponseMetadata {
    pub retry_after: Option<Duration>,
    pub rate_limit_headers: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct HttpClientDiagnostics {
    pub last_rate_limit_headers: BTreeMap<String, String>,
}

/// Retry semantics are selected by the Integration capability, not inferred
/// from the HTTP method. Some providers use POST for read-only queries, while
/// order, transfer, and account mutation requests must not be replayed after
/// an ambiguous response.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HttpRequestSemantics {
    Query,
    Command,
}

impl HttpRequestSemantics {
    const fn max_attempts(self) -> usize {
        match self {
            Self::Query => 3,
            Self::Command => 1,
        }
    }
}

/// Default async HTTP client. It creates no runtime or worker thread; request
/// futures are polled by the caller's runtime. Clone and reuse it so provider
/// capability projections share reqwest's connection pool.
#[derive(Clone)]
pub struct HttpClient {
    client: reqwest::Client,
    diagnostics: Arc<Mutex<HttpClientDiagnostics>>,
}

impl HttpClient {
    pub fn new(user_agent: &str) -> Result<Self, ExchangeError> {
        reqwest::Client::builder()
            .user_agent(user_agent)
            .timeout(Duration::from_secs(120))
            .build()
            .map(|client| Self {
                client,
                diagnostics: Arc::new(Mutex::new(HttpClientDiagnostics::default())),
            })
            .map_err(ExchangeError::Transport)
    }

    pub fn diagnostics(&self) -> HttpClientDiagnostics {
        self.diagnostics
            .lock()
            .map(|value| value.clone())
            .unwrap_or_default()
    }

    fn observe(&self, metadata: &HttpResponseMetadata) {
        if metadata.rate_limit_headers.is_empty() {
            return;
        }
        if let Ok(mut diagnostics) = self.diagnostics.lock() {
            diagnostics.last_rate_limit_headers = metadata.rate_limit_headers.clone();
        }
    }

    pub async fn get_json_response_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<HttpJsonResponse, ExchangeError> {
        self.request_json_response(
            Method::GET,
            endpoint,
            query,
            headers,
            HttpRequestSemantics::Query,
        )
        .await
    }

    pub async fn post_json_response_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<HttpJsonResponse, ExchangeError> {
        self.request_json_response(
            Method::POST,
            endpoint,
            query,
            headers,
            HttpRequestSemantics::Command,
        )
        .await
    }

    pub async fn put_query_json_response_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<HttpJsonResponse, ExchangeError> {
        self.request_json_response(
            Method::PUT,
            endpoint,
            query,
            headers,
            HttpRequestSemantics::Query,
        )
        .await
    }

    pub async fn put_command_json_response_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<HttpJsonResponse, ExchangeError> {
        self.request_json_response(
            Method::PUT,
            endpoint,
            query,
            headers,
            HttpRequestSemantics::Command,
        )
        .await
    }

    /// Sends a semantically read-only provider query represented as a JSON
    /// POST body. The caller runtime drives retries and response parsing.
    pub async fn post_query_json_with_headers(
        &self,
        endpoint: &str,
        headers: &[(&str, String)],
        body: &Value,
    ) -> Result<Value, ExchangeError> {
        let mut last_error = None;
        for attempt in 0..HttpRequestSemantics::Query.max_attempts() {
            let request = headers.iter().fold(
                self.client.post(endpoint).json(body),
                |request, (name, value)| request.header(*name, value),
            );
            match request.send().await {
                Ok(response) if response.status().is_success() => {
                    return parse_async_response_with_metadata(response)
                        .await
                        .map(|response| {
                            self.observe(&response.metadata);
                            response.body
                        });
                }
                Ok(response) => {
                    let status = response.status().as_u16();
                    let metadata = response_metadata(response.headers());
                    self.observe(&metadata);
                    let body = response.text().await.unwrap_or_default();
                    if status < 500 && status != 429 {
                        return Err(ExchangeError::Http {
                            status,
                            body,
                            metadata,
                        });
                    }
                    last_error = Some(ExchangeError::Http {
                        status,
                        body,
                        metadata: metadata.clone(),
                    });
                    if attempt + 1 < HttpRequestSemantics::Query.max_attempts() {
                        tokio::time::sleep(query_retry_delay(endpoint, attempt, &metadata)).await;
                        continue;
                    }
                }
                Err(error) => last_error = Some(ExchangeError::Transport(error)),
            }
            if attempt + 1 < HttpRequestSemantics::Query.max_attempts() {
                tokio::time::sleep(query_retry_delay(
                    endpoint,
                    attempt,
                    &HttpResponseMetadata::default(),
                ))
                .await;
            }
        }
        Err(last_error.expect("at least one HTTP attempt"))
    }

    /// Send a JSON command body exactly once. A response loss is ambiguous;
    /// callers must classify it through their command outcome policy and must
    /// never transparently replay this request.
    pub async fn post_json_command_with_headers(
        &self,
        endpoint: &str,
        headers: &[(&str, String)],
        body: &Value,
    ) -> Result<HttpJsonResponse, ExchangeError> {
        let request = headers.iter().fold(
            self.client.post(endpoint).json(body),
            |request, (name, value)| request.header(*name, value),
        );
        match request.send().await {
            Ok(response) if response.status().is_success() => {
                parse_async_response_with_metadata(response)
                    .await
                    .inspect(|response| self.observe(&response.metadata))
            }
            Ok(response) => {
                let status = response.status().as_u16();
                let metadata = response_metadata(response.headers());
                self.observe(&metadata);
                let body = response.text().await.unwrap_or_default();
                Err(ExchangeError::Http {
                    status,
                    body,
                    metadata,
                })
            }
            Err(error) => Err(ExchangeError::Transport(error)),
        }
    }

    pub async fn delete_json_response_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<HttpJsonResponse, ExchangeError> {
        self.request_json_response(
            Method::DELETE,
            endpoint,
            query,
            headers,
            HttpRequestSemantics::Command,
        )
        .await
    }

    async fn request_json_response(
        &self,
        method: Method,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
        semantics: HttpRequestSemantics,
    ) -> Result<HttpJsonResponse, ExchangeError> {
        let mut last_error = None;
        for attempt in 0..semantics.max_attempts() {
            let request = headers.iter().fold(
                self.client.request(method.clone(), endpoint).query(query),
                |request, (name, value)| request.header(*name, value),
            );
            match request.send().await {
                Ok(response) if response.status().is_success() => {
                    return parse_async_response_with_metadata(response)
                        .await
                        .inspect(|response| self.observe(&response.metadata));
                }
                Ok(response) => {
                    let status = response.status().as_u16();
                    let metadata = response_metadata(response.headers());
                    self.observe(&metadata);
                    let body = response.text().await.unwrap_or_default();
                    if status < 500 && status != 429 {
                        return Err(ExchangeError::Http {
                            status,
                            body,
                            metadata,
                        });
                    }
                    last_error = Some(ExchangeError::Http {
                        status,
                        body,
                        metadata: metadata.clone(),
                    });
                    if attempt + 1 < semantics.max_attempts() {
                        tokio::time::sleep(query_retry_delay(endpoint, attempt, &metadata)).await;
                        continue;
                    }
                }
                Err(error) => last_error = Some(ExchangeError::Transport(error)),
            }
            if attempt + 1 < semantics.max_attempts() {
                tokio::time::sleep(query_retry_delay(
                    endpoint,
                    attempt,
                    &HttpResponseMetadata::default(),
                ))
                .await;
            }
        }
        Err(last_error.expect("at least one HTTP attempt"))
    }
}

async fn parse_async_response_with_metadata(
    response: reqwest::Response,
) -> Result<HttpJsonResponse, ExchangeError> {
    let metadata = response_metadata(response.headers());
    let body = response.text().await?;
    let body = if body.trim().is_empty() {
        Value::Null
    } else {
        serde_json::from_str(&body).map_err(|error| ExchangeError::InvalidJson {
            message: error.to_string(),
            body: diagnostic_body(&body),
        })?
    };
    Ok(HttpJsonResponse { body, metadata })
}

fn response_metadata(headers: &reqwest::header::HeaderMap) -> HttpResponseMetadata {
    let mut metadata = HttpResponseMetadata::default();
    for (name, value) in headers {
        let name = name.as_str().to_ascii_lowercase();
        if name == "retry-after" {
            metadata.retry_after = value
                .to_str()
                .ok()
                .and_then(|value| value.trim().parse::<u64>().ok())
                .map(Duration::from_secs);
        }
        if is_rate_limit_header(&name) {
            if let Ok(value) = value.to_str() {
                metadata.rate_limit_headers.insert(name, value.to_owned());
            }
        }
    }
    metadata
}

fn is_rate_limit_header(name: &str) -> bool {
    name == "retry-after"
        || name.starts_with("x-ratelimit-")
        || name.starts_with("x-rate-limit-")
        || name.starts_with("x-mbx-used-weight")
        || name.starts_with("x-mbx-order-count")
}

fn query_retry_delay(endpoint: &str, attempt: usize, metadata: &HttpResponseMetadata) -> Duration {
    const MAX_DELAY: Duration = Duration::from_secs(10);
    if let Some(delay) = metadata.retry_after {
        return delay.min(MAX_DELAY);
    }
    let exponential_millis = 250_u64.saturating_mul(1_u64 << attempt.min(5));
    let endpoint_hash = endpoint.bytes().fold(0_u64, |hash, byte| {
        hash.wrapping_mul(31).wrapping_add(u64::from(byte))
    });
    let jitter_millis = endpoint_hash.wrapping_add(attempt as u64 * 17) % 126;
    Duration::from_millis(exponential_millis + jitter_millis).min(MAX_DELAY)
}

fn diagnostic_body(body: &str) -> String {
    const LIMIT: usize = 512;
    let normalized = body.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.chars().count() <= LIMIT {
        normalized
    } else {
        format!("{}…", normalized.chars().take(LIMIT).collect::<String>())
    }
}

#[cfg(test)]
mod tests {
    use super::{command_error_outcome, ExchangeError, HttpClient};
    use crate::{CommandOutcome, IntegrationError};
    use serde_json::json;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::time::Duration;

    fn json_server(statuses: Vec<u16>) -> (String, Arc<AtomicUsize>, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("test server binds");
        let address = listener.local_addr().expect("test server address");
        let count = Arc::new(AtomicUsize::new(0));
        let server_count = count.clone();
        let handle = std::thread::spawn(move || {
            for status in statuses {
                let (mut stream, _) = listener.accept().expect("test request accepted");
                let mut request = [0_u8; 4096];
                let _ = stream.read(&mut request).expect("test request read");
                server_count.fetch_add(1, Ordering::SeqCst);
                let (reason, body) = match status {
                    200 => ("OK", r#"{"ok":true}"#),
                    429 => ("Too Many Requests", r#"{"error":"rate limited"}"#),
                    _ => ("Internal Server Error", r#"{"error":"temporary"}"#),
                };
                write!(
                    stream,
                    "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nRetry-After: 0\r\nX-MBX-USED-WEIGHT-1M: 42\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .expect("test response written");
            }
        });
        (format!("http://{address}"), count, handle)
    }

    fn dropped_response_server() -> (String, Arc<AtomicUsize>, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("test server binds");
        let address = listener.local_addr().expect("test server address");
        let count = Arc::new(AtomicUsize::new(0));
        let server_count = count.clone();
        let handle = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("test request accepted");
            let mut request = [0_u8; 4096];
            let _ = stream.read(&mut request).expect("test request read");
            server_count.fetch_add(1, Ordering::SeqCst);
            // Model a provider which may have accepted the command but loses
            // the response before an acknowledgement reaches the caller.
        });
        (format!("http://{address}"), count, handle)
    }

    #[tokio::test]
    async fn async_command_is_never_transparently_retried() {
        let (endpoint, count, server) = json_server(vec![500]);
        let client = HttpClient::new("kairos-async-http-command-test").unwrap();

        let error = client
            .post_json_response_with_headers_and_query(&endpoint, &[], &[])
            .await
            .expect_err("command returns the first provider error");

        assert!(matches!(error, ExchangeError::Http { status: 500, .. }));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn put_command_is_never_transparently_retried() {
        let (endpoint, count, server) = json_server(vec![500]);
        let client = HttpClient::new("kairos-async-http-put-command-test").unwrap();

        let error = client
            .put_command_json_response_with_headers_and_query(&endpoint, &[], &[])
            .await
            .expect_err("command returns the first provider error");

        assert!(matches!(error, ExchangeError::Http { status: 500, .. }));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn rate_limited_command_is_attempted_once() {
        let (endpoint, count, server) = json_server(vec![429]);
        let client = HttpClient::new("kairos-http-command-rate-test").unwrap();

        let error = client
            .post_json_response_with_headers_and_query(&endpoint, &[], &[])
            .await
            .expect_err("command returns the first rate-limit response");

        assert!(matches!(
            error,
            super::ExchangeError::Http { status: 429, .. }
        ));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn response_lost_after_command_write_is_indeterminate_and_not_retried() {
        let (endpoint, count, server) = dropped_response_server();
        let client = HttpClient::new("kairos-http-command-response-loss-test").unwrap();

        let error = client
            .post_json_response_with_headers_and_query(&endpoint, &[], &[])
            .await
            .expect_err("connection closes before the command response");
        let outcome = command_error_outcome::<()>(error).unwrap();

        assert!(matches!(outcome, CommandOutcome::Indeterminate(_)));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn async_query_retries_transient_provider_errors() {
        let (endpoint, count, server) = json_server(vec![500, 200]);
        let client = HttpClient::new("kairos-async-http-query-test").unwrap();

        let response = client
            .get_json_response_with_headers_and_query(&endpoint, &[], &[])
            .await
            .unwrap()
            .body;

        assert_eq!(response, json!({"ok": true}));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn async_query_preserves_sanitized_rate_limit_evidence() {
        let (endpoint, count, server) = json_server(vec![200]);
        let client = HttpClient::new("kairos-http-rate-evidence-test").unwrap();

        let response = client
            .get_json_response_with_headers_and_query(&endpoint, &[], &[])
            .await
            .unwrap();

        assert_eq!(response.metadata.retry_after, Some(Duration::ZERO));
        assert_eq!(
            response
                .metadata
                .rate_limit_headers
                .get("x-mbx-used-weight-1m")
                .map(String::as_str),
            Some("42")
        );
        assert_eq!(
            client
                .diagnostics()
                .last_rate_limit_headers
                .get("x-mbx-used-weight-1m")
                .map(String::as_str),
            Some("42")
        );
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn read_only_post_retries_only_when_explicitly_marked_as_query() {
        let (endpoint, count, server) = json_server(vec![429, 200]);
        let client = HttpClient::new("kairos-http-post-query-test").unwrap();

        let response = client
            .post_query_json_with_headers(&endpoint, &[], &json!({"type": "metadata"}))
            .await
            .unwrap();

        assert_eq!(response, json!({"ok": true}));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn explicit_command_rejection_is_not_indeterminate() {
        let outcome = command_error_outcome::<()>(ExchangeError::Http {
            status: 400,
            body: r#"{"code":-1013,"msg":"invalid quantity"}"#.into(),
            metadata: Default::default(),
        })
        .unwrap();

        let CommandOutcome::Rejected(rejection) = outcome else {
            panic!("explicit provider response must be a rejection");
        };
        assert_eq!(rejection.code.as_deref(), Some("-1013"));
        assert_eq!(rejection.message, "invalid quantity");
    }

    #[test]
    fn authentication_failure_is_classified_without_reconciliation() {
        let error = command_error_outcome::<()>(ExchangeError::Http {
            status: 401,
            body: r#"{"code":"invalid-api-key"}"#.into(),
            metadata: Default::default(),
        })
        .unwrap_err();

        assert!(matches!(error, IntegrationError::Authentication(_)));
    }

    #[test]
    fn transient_command_response_is_indeterminate() {
        let outcome = command_error_outcome::<()>(ExchangeError::Http {
            status: 500,
            body: r#"{"error":"temporary"}"#.into(),
            metadata: Default::default(),
        })
        .unwrap();

        assert!(matches!(outcome, CommandOutcome::Indeterminate(_)));
    }

    #[test]
    fn local_invalid_command_stays_an_application_error() {
        let error =
            command_error_outcome::<()>(ExchangeError::InvalidRequest("missing symbol".into()))
                .unwrap_err();

        assert!(matches!(error, IntegrationError::InvalidRequest(_)));
    }
}
