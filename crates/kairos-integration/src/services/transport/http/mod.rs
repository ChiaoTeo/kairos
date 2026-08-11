//! Provider-neutral HTTP foundation for exchange adapters.
//!
//! This crate deliberately knows nothing about markets, accounts, or
//! FlatBuffers. Exchange-specific crates convert its JSON result into their
//! own provider records.

use std::collections::BTreeMap;
use std::sync::{
    mpsc::{self, Sender},
    Arc, Mutex,
};
use std::time::Duration;

use reqwest::{blocking::Client, Method};
use serde_json::Value;
use thiserror::Error;

use crate::application::{
    CommandOutcome, IndeterminateCommand, IntegrationError, ProviderRejection,
};

#[derive(Debug, Error)]
pub enum ExchangeError {
    #[error("HTTP request failed: {0}")]
    Transport(#[from] reqwest::Error),
    #[error("exchange returned HTTP status {status}: {body}")]
    Http { status: u16, body: String },
    #[error("invalid exchange response: {0}")]
    Response(#[from] serde_json::Error),
    #[error("exchange returned invalid JSON: {message}; body: {body}")]
    InvalidJson { message: String, body: String },
    #[error("exchange authentication failed: {0}")]
    Authentication(String),
    #[error("invalid exchange request: {0}")]
    InvalidRequest(String),
    #[error("exchange connection failed: {0}")]
    Connection(String),
    /// A provider prerequisite failed before the intended command request was
    /// created or written. This must never be converted into an indeterminate
    /// command outcome.
    #[error("exchange request preflight failed: {0}")]
    Preflight(String),
    #[error("local provider rate limit: {message}; retry after {retry_after_millis}ms")]
    LocalRateLimit {
        retry_after_millis: u64,
        message: String,
    },
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
        ExchangeError::LocalRateLimit { message, .. } => {
            Err(IntegrationError::RateLimited(message))
        }
        ExchangeError::Preflight(message) => Err(IntegrationError::Unavailable(message)),
        ExchangeError::Http { status: 401, body } => {
            Err(IntegrationError::Authentication(diagnostic_body(&body)))
        }
        ExchangeError::Http { status: 403, body } => {
            Err(IntegrationError::Authorization(diagnostic_body(&body)))
        }
        ExchangeError::Http { status, body }
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

fn provider_rejection(status: u16, body: &str) -> ProviderRejection {
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
    ProviderRejection {
        code,
        message,
        provider_request_id: None,
    }
}

type HttpJob = Box<dyn FnOnce(&Client) + Send + 'static>;

#[derive(Clone, Debug)]
pub struct HttpJsonResponse {
    pub body: Value,
    /// Lower-case response headers. Provider adapters must only inspect
    /// documented non-secret metadata and must not forward this map across
    /// the Integration application boundary.
    pub headers: BTreeMap<String, String>,
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

#[derive(Clone)]
pub struct PublicHttpClient {
    /// The blocking client and all blocking I/O live on this dedicated
    /// worker. Callers may be synchronous today, but async Tokio handlers
    /// never construct, use, or drop reqwest's private runtime directly.
    worker: Arc<Mutex<Option<Sender<HttpJob>>>>,
    user_agent: Arc<str>,
}

/// Default async HTTP client. It creates no runtime or worker thread; request
/// futures are polled by the caller's runtime. Clone and reuse it so provider
/// capability projections share reqwest's connection pool.
#[derive(Clone)]
pub struct AsyncPublicHttpClient {
    client: reqwest::Client,
}

impl AsyncPublicHttpClient {
    pub fn new(user_agent: &str) -> Result<Self, ExchangeError> {
        reqwest::Client::builder()
            .user_agent(user_agent)
            .timeout(Duration::from_secs(120))
            .build()
            .map(|client| Self { client })
            .map_err(ExchangeError::Transport)
    }

    pub async fn get_json(&self, endpoint: &str) -> Result<Value, ExchangeError> {
        self.get_json_response_with_headers_and_query(endpoint, &[], &[])
            .await
            .map(|response| response.body)
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

    pub async fn post_query_json_response_with_headers_and_query(
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
            HttpRequestSemantics::Query,
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
                        .map(|response| response.body)
                }
                Ok(response) => {
                    let status = response.status().as_u16();
                    let body = response.text().await.unwrap_or_default();
                    if status < 500 && status != 429 {
                        return Err(ExchangeError::Http { status, body });
                    }
                    last_error = Some(ExchangeError::Http { status, body });
                }
                Err(error) => last_error = Some(ExchangeError::Transport(error)),
            }
            if attempt + 1 < HttpRequestSemantics::Query.max_attempts() {
                tokio::time::sleep(Duration::from_millis(250)).await;
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
                parse_async_response_with_metadata(response).await
            }
            Ok(response) => {
                let status = response.status().as_u16();
                let body = response.text().await.unwrap_or_default();
                Err(ExchangeError::Http { status, body })
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
                    return parse_async_response_with_metadata(response).await
                }
                Ok(response) => {
                    let status = response.status().as_u16();
                    let body = response.text().await.unwrap_or_default();
                    if status < 500 && status != 429 {
                        return Err(ExchangeError::Http { status, body });
                    }
                    last_error = Some(ExchangeError::Http { status, body });
                }
                Err(error) => last_error = Some(ExchangeError::Transport(error)),
            }
            if attempt + 1 < semantics.max_attempts() {
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
        }
        Err(last_error.expect("at least one HTTP attempt"))
    }
}

impl PublicHttpClient {
    pub fn new(user_agent: &str) -> Result<Self, ExchangeError> {
        if user_agent.trim().is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "HTTP user agent is required".into(),
            ));
        }
        Ok(Self {
            worker: Arc::new(Mutex::new(None)),
            user_agent: Arc::from(user_agent),
        })
    }

    fn worker(&self) -> Result<Sender<HttpJob>, ExchangeError> {
        let mut worker = self
            .worker
            .lock()
            .map_err(|_| ExchangeError::Connection("HTTP worker lock is poisoned".into()))?;
        if let Some(sender) = worker.as_ref() {
            return Ok(sender.clone());
        }
        let user_agent = self.user_agent.to_string();
        let (sender, receiver) = mpsc::channel::<HttpJob>();
        let (ready_sender, ready_receiver) = mpsc::sync_channel(1);
        std::thread::Builder::new()
            .name("kairos-http-blocking".into())
            .spawn(move || {
                let client = match Client::builder()
                    .user_agent(user_agent)
                    // Reference exchangeInfo responses can be tens of megabytes
                    // uncompressed. Keep enough time for slow public API routes.
                    .timeout(Duration::from_secs(120))
                    .build()
                {
                    Ok(client) => client,
                    Err(error) => {
                        let _ = ready_sender.send(Err(error.to_string()));
                        return;
                    }
                };
                let _ = ready_sender.send(Ok(()));
                while let Ok(job) = receiver.recv() {
                    // The worker owns the blocking client and is the only place
                    // where provider REST requests are executed.
                    job(&client);
                }
            })
            .map_err(|error| ExchangeError::Connection(error.to_string()))?;
        ready_receiver
            .recv()
            .map_err(|error| ExchangeError::Connection(error.to_string()))?
            .map_err(ExchangeError::Connection)?;
        *worker = Some(sender.clone());
        Ok(sender)
    }

    #[cfg(test)]
    pub(crate) fn shares_worker_with(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.worker, &other.worker)
    }

    #[cfg(test)]
    fn worker_started(&self) -> bool {
        self.worker
            .lock()
            .map(|worker| worker.is_some())
            .unwrap_or(false)
    }

    pub fn get_json(&self, endpoint: &str) -> Result<Value, ExchangeError> {
        self.get_json_with_query(endpoint, &[])
    }

    pub fn get_json_with_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
    ) -> Result<Value, ExchangeError> {
        self.get_json_with_headers_and_query(endpoint, query, &[])
    }

    pub fn get_json_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<Value, ExchangeError> {
        self.get_json_response_with_headers_and_query(endpoint, query, headers)
            .map(|response| response.body)
    }

    pub fn get_json_response_with_headers_and_query(
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
    }

    pub fn post_json_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<Value, ExchangeError> {
        self.post_json_response_with_headers_and_query(endpoint, query, headers)
            .map(|response| response.body)
    }

    pub fn post_json_response_with_headers_and_query(
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
    }

    /// Sends a read-only query encoded by the provider as POST with query
    /// parameters. Callers must opt in; ordinary POST remains a command.
    pub fn post_query_json_response_with_headers_and_query(
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
            HttpRequestSemantics::Query,
        )
    }

    pub fn delete_json_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<Value, ExchangeError> {
        self.delete_json_response_with_headers_and_query(endpoint, query, headers)
            .map(|response| response.body)
    }

    pub fn delete_json_response_with_headers_and_query(
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
    }

    pub fn post_json_with_headers(
        &self,
        endpoint: &str,
        headers: &[(&str, String)],
        body: &Value,
    ) -> Result<Value, ExchangeError> {
        let endpoint = endpoint.to_owned();
        let headers: Vec<(String, String)> = headers
            .iter()
            .map(|(name, value)| ((*name).to_owned(), value.clone()))
            .collect::<Vec<_>>();
        let body = body.clone();
        self.execute(move |client| {
            perform_post(
                client,
                &endpoint,
                &headers,
                &body,
                HttpRequestSemantics::Command,
            )
        })
    }

    /// Sends a provider operation which is semantically read-only even
    /// though the provider models it as HTTP POST.
    pub fn post_query_json_with_headers(
        &self,
        endpoint: &str,
        headers: &[(&str, String)],
        body: &Value,
    ) -> Result<Value, ExchangeError> {
        let endpoint = endpoint.to_owned();
        let headers: Vec<(String, String)> = headers
            .iter()
            .map(|(name, value)| ((*name).to_owned(), value.clone()))
            .collect::<Vec<_>>();
        let body = body.clone();
        self.execute(move |client| {
            perform_post(
                client,
                &endpoint,
                &headers,
                &body,
                HttpRequestSemantics::Query,
            )
        })
    }

    fn request_json_response(
        &self,
        method: Method,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
        semantics: HttpRequestSemantics,
    ) -> Result<HttpJsonResponse, ExchangeError> {
        let endpoint = endpoint.to_owned();
        let query: Vec<(String, String)> = query
            .iter()
            .map(|(name, value)| ((*name).to_owned(), value.clone()))
            .collect::<Vec<_>>();
        let headers: Vec<(String, String)> = headers
            .iter()
            .map(|(name, value)| ((*name).to_owned(), value.clone()))
            .collect::<Vec<_>>();
        self.execute(move |client| {
            perform_request(client, method, &endpoint, &query, &headers, semantics)
        })
    }

    fn execute<T, F>(&self, operation: F) -> Result<T, ExchangeError>
    where
        T: Send + 'static,
        F: FnOnce(&Client) -> Result<T, ExchangeError> + Send + 'static,
    {
        let (sender, receiver) = mpsc::sync_channel(1);
        self.worker()?
            .send(Box::new(move |client| {
                let result = operation(client);
                let _ = sender.send(result);
            }))
            .map_err(|error| ExchangeError::Connection(error.to_string()))?;
        receiver
            .recv()
            .map_err(|error| ExchangeError::Connection(error.to_string()))?
    }
}

fn perform_post(
    client: &Client,
    endpoint: &str,
    headers: &[(String, String)],
    body: &Value,
    semantics: HttpRequestSemantics,
) -> Result<Value, ExchangeError> {
    let mut last_error = None;
    let max_attempts = semantics.max_attempts();
    for attempt in 0..max_attempts {
        let request = headers.iter().fold(
            client.post(endpoint).json(body),
            |request, (name, value)| request.header(name, value),
        );
        match request.send() {
            Ok(response) if response.status().is_success() => return parse_response(response),
            Ok(response) => {
                let status = response.status().as_u16();
                let body = response.text().unwrap_or_default();
                if status < 500 && status != 429 {
                    return Err(ExchangeError::Http { status, body });
                }
                last_error = Some(ExchangeError::Http { status, body });
            }
            Err(error) => last_error = Some(ExchangeError::Transport(error)),
        }
        if attempt + 1 < max_attempts {
            std::thread::sleep(Duration::from_millis(250));
        }
    }
    Err(last_error.expect("at least one HTTP attempt"))
}

fn perform_request(
    client: &Client,
    method: Method,
    endpoint: &str,
    query: &[(String, String)],
    headers: &[(String, String)],
    semantics: HttpRequestSemantics,
) -> Result<HttpJsonResponse, ExchangeError> {
    let mut last_error = None;
    let max_attempts = semantics.max_attempts();
    for attempt in 0..max_attempts {
        let request = headers.iter().fold(
            client.request(method.clone(), endpoint).query(query),
            |request, (name, value)| request.header(name, value),
        );
        match request.send() {
            Ok(response) if response.status().is_success() => {
                return parse_response_with_metadata(response)
            }
            Ok(response) => {
                let status = response.status().as_u16();
                let body = response.text().unwrap_or_default();
                if status < 500 && status != 429 {
                    return Err(ExchangeError::Http { status, body });
                }
                last_error = Some(ExchangeError::Http { status, body });
            }
            Err(error) => last_error = Some(ExchangeError::Transport(error)),
        }
        if attempt + 1 < max_attempts {
            std::thread::sleep(Duration::from_millis(250));
        }
    }
    Err(last_error.expect("at least one HTTP attempt"))
}

fn parse_response(response: reqwest::blocking::Response) -> Result<Value, ExchangeError> {
    let body = response.text()?;
    serde_json::from_str(&body).map_err(|error| ExchangeError::InvalidJson {
        message: error.to_string(),
        body: diagnostic_body(&body),
    })
}

fn parse_response_with_metadata(
    response: reqwest::blocking::Response,
) -> Result<HttpJsonResponse, ExchangeError> {
    let headers = response
        .headers()
        .iter()
        .filter_map(|(name, value)| {
            value
                .to_str()
                .ok()
                .map(|value| (name.as_str().to_ascii_lowercase(), value.to_owned()))
        })
        .collect();
    let body = response.text()?;
    let body = serde_json::from_str(&body).map_err(|error| ExchangeError::InvalidJson {
        message: error.to_string(),
        body: diagnostic_body(&body),
    })?;
    Ok(HttpJsonResponse { body, headers })
}

async fn parse_async_response_with_metadata(
    response: reqwest::Response,
) -> Result<HttpJsonResponse, ExchangeError> {
    let headers = response
        .headers()
        .iter()
        .filter_map(|(name, value)| {
            value
                .to_str()
                .ok()
                .map(|value| (name.as_str().to_ascii_lowercase(), value.to_owned()))
        })
        .collect();
    let body = response.text().await?;
    let body = serde_json::from_str(&body).map_err(|error| ExchangeError::InvalidJson {
        message: error.to_string(),
        body: diagnostic_body(&body),
    })?;
    Ok(HttpJsonResponse { body, headers })
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
    use super::{command_error_outcome, AsyncPublicHttpClient, ExchangeError, PublicHttpClient};
    use crate::application::{CommandOutcome, IntegrationError};
    use serde_json::json;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

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
                    "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .expect("test response written");
            }
        });
        (format!("http://{address}"), count, handle)
    }

    #[tokio::test(flavor = "current_thread")]
    async fn constructing_blocking_projection_does_not_start_a_hidden_worker() {
        let client = PublicHttpClient::new("kairos-lazy-blocking-http-test").unwrap();
        assert!(!client.worker_started());
        tokio::task::yield_now().await;
        assert!(!client.worker_started());
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

    #[test]
    fn command_post_is_never_transparently_retried() {
        let (endpoint, count, server) = json_server(vec![500]);
        let client = PublicHttpClient::new("kairos-http-command-test").unwrap();

        let error = client
            .post_json_with_headers(&endpoint, &[], &json!({"order": "one"}))
            .expect_err("command returns the first provider error");

        assert!(matches!(
            error,
            super::ExchangeError::Http { status: 500, .. }
        ));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn async_command_is_never_transparently_retried() {
        let (endpoint, count, server) = json_server(vec![500]);
        let client = AsyncPublicHttpClient::new("kairos-async-http-command-test").unwrap();

        let error = client
            .post_json_response_with_headers_and_query(&endpoint, &[], &[])
            .await
            .expect_err("command returns the first provider error");

        assert!(matches!(error, ExchangeError::Http { status: 500, .. }));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn rate_limited_command_is_attempted_once() {
        let (endpoint, count, server) = json_server(vec![429]);
        let client = PublicHttpClient::new("kairos-http-command-rate-test").unwrap();

        let error = client
            .post_json_with_headers(&endpoint, &[], &json!({"order": "one"}))
            .expect_err("command returns the first rate-limit response");

        assert!(matches!(
            error,
            super::ExchangeError::Http { status: 429, .. }
        ));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn response_lost_after_command_write_is_indeterminate_and_not_retried() {
        let (endpoint, count, server) = dropped_response_server();
        let client = PublicHttpClient::new("kairos-http-command-response-loss-test").unwrap();

        let error = client
            .post_json_with_headers(&endpoint, &[], &json!({"order": "one"}))
            .expect_err("connection closes before the command response");
        let outcome = command_error_outcome::<()>(error).unwrap();

        assert!(matches!(outcome, CommandOutcome::Indeterminate(_)));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn query_get_retries_transient_provider_errors() {
        let (endpoint, count, server) = json_server(vec![500, 200]);
        let client = PublicHttpClient::new("kairos-http-query-test").unwrap();

        let response = client.get_json(&endpoint).unwrap();

        assert_eq!(response, json!({"ok": true}));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn async_query_retries_transient_provider_errors() {
        let (endpoint, count, server) = json_server(vec![500, 200]);
        let client = AsyncPublicHttpClient::new("kairos-async-http-query-test").unwrap();

        let response = client.get_json(&endpoint).await.unwrap();

        assert_eq!(response, json!({"ok": true}));
        server.join().unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn read_only_post_retries_only_when_explicitly_marked_as_query() {
        let (endpoint, count, server) = json_server(vec![429, 200]);
        let client = PublicHttpClient::new("kairos-http-post-query-test").unwrap();

        let response = client
            .post_query_json_with_headers(&endpoint, &[], &json!({"type": "metadata"}))
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
        })
        .unwrap_err();

        assert!(matches!(error, IntegrationError::Authentication(_)));
    }

    #[test]
    fn transient_command_response_is_indeterminate() {
        let outcome = command_error_outcome::<()>(ExchangeError::Http {
            status: 500,
            body: r#"{"error":"temporary"}"#.into(),
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

    #[test]
    fn failed_command_preflight_is_proven_not_sent() {
        let result = command_error_outcome::<()>(ExchangeError::Preflight(
            "provider clock is unavailable".into(),
        ));
        assert!(matches!(result, Err(IntegrationError::Unavailable(_))));
    }
}
