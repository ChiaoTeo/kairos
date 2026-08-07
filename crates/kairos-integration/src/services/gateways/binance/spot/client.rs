//! Binance Spot REST client composition boundary.

use serde_json::Value;

use crate::services::drivers::http::{ExchangeError, PublicHttpClient};

pub struct SpotRestClient {
    http: PublicHttpClient,
    exchange_info_endpoint: String,
}

impl SpotRestClient {
    pub fn new(endpoint: impl Into<String>) -> Result<Self, ExchangeError> {
        let endpoint = endpoint.into();
        if endpoint.trim().is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Binance Spot exchangeInfo endpoint is required".into(),
            ));
        }
        Ok(Self {
            http: PublicHttpClient::new("kairos-integration/binance-spot")?,
            exchange_info_endpoint: endpoint,
        })
    }

    pub fn exchange_info(&self) -> Result<Value, ExchangeError> {
        self.http.get_json(&self.exchange_info_endpoint)
    }
}

pub trait SpotClient {
    fn exchange_info(&self) -> Result<Value, ExchangeError>;
}

impl SpotClient for SpotRestClient {
    fn exchange_info(&self) -> Result<Value, ExchangeError> {
        self.exchange_info()
    }
}
