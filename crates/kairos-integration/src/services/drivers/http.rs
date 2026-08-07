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
    #[error("exchange authentication failed: {0}")]
    Authentication(String),
    #[error("invalid exchange request: {0}")]
    InvalidRequest(String),
    #[error("exchange connection failed: {0}")]
    Connection(String),
}

#[derive(Clone)]
pub struct PublicHttpClient {
    client: Client,
    max_attempts: usize,
    retry_delay: Duration,
}

impl PublicHttpClient {
    pub fn new(user_agent: &str) -> Result<Self, ExchangeError> {
        let client = Client::builder().user_agent(user_agent).build()?;
        Ok(Self {
            client,
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
                self.client.post(endpoint).json(body),
                |request, (name, value)| request.header(*name, value),
            );
            match request.send() {
                Ok(response) if response.status().is_success() => return Ok(response.json()?),
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
                self.client.request(method.clone(), endpoint).query(query),
                |request, (name, value)| request.header(*name, value),
            );
            match request.send() {
                Ok(response) if response.status().is_success() => return Ok(response.json()?),
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
