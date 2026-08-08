//! Provider-neutral HTTP foundation for exchange adapters.
//!
//! This crate deliberately knows nothing about markets, accounts, or
//! FlatBuffers. Exchange-specific crates convert its JSON result into their
//! own provider records.

use std::time::Duration;

use reqwest::{blocking::Client, Method};
use serde_json::Value;
use thiserror::Error;

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
}

#[derive(Clone)]
pub struct PublicHttpClient {
    // reqwest::blocking owns an internal Tokio runtime.  Market processes
    // call it from an async server, so move its final drop off the Tokio
    // worker thread (otherwise Tokio panics during shutdown).
    client: Option<Client>,
    max_attempts: usize,
    retry_delay: Duration,
}

impl PublicHttpClient {
    pub fn new(user_agent: &str) -> Result<Self, ExchangeError> {
        // reqwest::blocking refuses to construct its private runtime while a
        // Tokio runtime is current. Market control handlers are async, so do
        // the blocking-client construction on a plain OS thread.
        let user_agent = user_agent.to_owned();
        let client = std::thread::spawn(move || {
            Client::builder()
                .user_agent(user_agent)
                // Reference exchangeInfo responses can be tens of megabytes
                // uncompressed. Keep enough time for slow public API routes.
                .timeout(Duration::from_secs(120))
                .build()
        })
        .join()
        .map_err(|_| ExchangeError::Connection("HTTP client builder thread panicked".into()))??;
        Ok(Self {
            client: Some(client),
            max_attempts: 3,
            retry_delay: Duration::from_millis(250),
        })
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
        self.request_json(Method::GET, endpoint, query, headers)
    }

    pub fn post_json_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<Value, ExchangeError> {
        self.request_json(Method::POST, endpoint, query, headers)
    }

    pub fn delete_json_with_headers_and_query(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<Value, ExchangeError> {
        self.request_json(Method::DELETE, endpoint, query, headers)
    }

    pub fn post_json_with_headers(
        &self,
        endpoint: &str,
        headers: &[(&str, String)],
        body: &Value,
    ) -> Result<Value, ExchangeError> {
        let mut last_error = None;
        for attempt in 0..self.max_attempts {
            let request = headers.iter().fold(
                self.client
                    .as_ref()
                    .expect("HTTP client is available")
                    .post(endpoint)
                    .json(body),
                |request, (name, value)| request.header(*name, value),
            );
            match request.send() {
                Ok(response) if response.status().is_success() => {
                    let body = response.text()?;
                    return serde_json::from_str(&body).map_err(|error| {
                        ExchangeError::InvalidJson {
                            message: error.to_string(),
                            body: diagnostic_body(&body),
                        }
                    });
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
            if attempt + 1 < self.max_attempts {
                std::thread::sleep(self.retry_delay);
            }
        }
        Err(last_error.expect("at least one HTTP attempt"))
    }

    fn request_json(
        &self,
        method: Method,
        endpoint: &str,
        query: &[(&str, String)],
        headers: &[(&str, String)],
    ) -> Result<Value, ExchangeError> {
        let mut last_error = None;
        for attempt in 0..self.max_attempts {
            let request = headers.iter().fold(
                self.client
                    .as_ref()
                    .expect("HTTP client is available")
                    .request(method.clone(), endpoint)
                    .query(query),
                |request, (name, value)| request.header(*name, value),
            );
            match request.send() {
                Ok(response) if response.status().is_success() => {
                    let body = response.text()?;
                    return serde_json::from_str(&body).map_err(|error| {
                        ExchangeError::InvalidJson {
                            message: error.to_string(),
                            body: diagnostic_body(&body),
                        }
                    });
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
            if attempt + 1 < self.max_attempts {
                std::thread::sleep(self.retry_delay);
            }
        }
        Err(last_error.expect("at least one HTTP attempt"))
    }
}

impl Drop for PublicHttpClient {
    fn drop(&mut self) {
        if let Some(client) = self.client.take() {
            std::thread::spawn(move || drop(client));
        }
    }
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
