//! Binance Stocks Trading signed REST client.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use reqwest::Method;
use serde_json::Value;

use crate::services::auth::signed_query;
use crate::services::drivers::http::{ExchangeError, PublicHttpClient};

pub struct BinanceEquityRestClient {
    http: PublicHttpClient,
    api_key: String,
    secret: String,
    base_url: String,
}

impl BinanceEquityRestClient {
    pub fn with_base_url(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, ExchangeError> {
        let api_key = api_key.into();
        let secret = secret.into();
        if api_key.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "Binance Equity API key is required".into(),
            ));
        }
        if secret.trim().is_empty() {
            return Err(ExchangeError::Authentication(
                "Binance Equity API secret is required".into(),
            ));
        }
        let base_url = base_url.into().trim_end_matches('/').to_string();
        if base_url.is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Binance Equity base URL is required".into(),
            ));
        }
        Ok(Self {
            http: PublicHttpClient::new("kairos-integration/binance-equity")?,
            api_key,
            secret,
            base_url,
        })
    }

    pub fn exchange_info(&self) -> Result<Value, ExchangeError> {
        self.get("/sapi/v1/equity/market/exchangeInfo", &[], false)
    }

    pub fn place_order(&self, params: &[(&str, String)]) -> Result<Value, ExchangeError> {
        self.post("/sapi/v1/equity/order/place", params)
    }

    pub fn cancel_order(&self, params: &[(&str, String)]) -> Result<Value, ExchangeError> {
        self.post("/sapi/v1/equity/order/cancel", params)
    }

    pub fn open_orders(&self, params: &[(&str, String)]) -> Result<Value, ExchangeError> {
        self.get("/sapi/v1/equity/order/open-orders", params, true)
    }

    pub fn order_history(&self, params: &[(&str, String)]) -> Result<Value, ExchangeError> {
        self.get("/sapi/v1/equity/order/history", params, true)
    }

    pub fn order_detail(&self, params: &[(&str, String)]) -> Result<Value, ExchangeError> {
        self.get("/sapi/v1/equity/order/detail", params, true)
    }

    fn get(
        &self,
        path: &str,
        params: &[(&str, String)],
        signed: bool,
    ) -> Result<Value, ExchangeError> {
        let mut values = BTreeMap::new();
        for (key, value) in params {
            values.insert((*key).to_string(), value.clone());
        }
        if signed {
            values.insert("timestamp".into(), now_millis().to_string());
            values.insert("recvWindow".into(), "10000".into());
        }
        let (query, signature) = if signed {
            let signed = signed_query(&self.secret, values.into_iter())?;
            (signed.query, Some(signed.signature))
        } else {
            let query = url::form_urlencoded::Serializer::new(String::new())
                .extend_pairs(values)
                .finish();
            (query, None)
        };
        let encoded: Vec<(String, String)> = url::form_urlencoded::parse(query.as_bytes())
            .map(|(key, value)| (key.into_owned(), value.into_owned()))
            .collect();
        let mut query_pairs: Vec<(&str, String)> = encoded
            .iter()
            .map(|(key, value)| (key.as_str(), value.clone()))
            .collect();
        if let Some(signature) = signature {
            query_pairs.push(("signature", signature));
        }
        self.request(Method::GET, path, &query_pairs)
    }

    fn post(&self, path: &str, params: &[(&str, String)]) -> Result<Value, ExchangeError> {
        let mut values = BTreeMap::new();
        for (key, value) in params {
            values.insert((*key).to_string(), value.clone());
        }
        values.insert("timestamp".into(), now_millis().to_string());
        values.insert("recvWindow".into(), "10000".into());
        let signed = signed_query(&self.secret, values.into_iter())?;
        let mut query_pairs: Vec<(String, String)> =
            url::form_urlencoded::parse(signed.query.as_bytes())
                .map(|(key, value)| (key.into_owned(), value.into_owned()))
                .collect();
        query_pairs.push(("signature".into(), signed.signature));
        let query_pairs: Vec<(&str, String)> = query_pairs
            .iter()
            .map(|(key, value)| (key.as_str(), value.clone()))
            .collect();
        self.request(Method::POST, path, &query_pairs)
    }

    fn request(
        &self,
        method: Method,
        path: &str,
        query: &[(&str, String)],
    ) -> Result<Value, ExchangeError> {
        let endpoint = format!("{}{}", self.base_url, path);
        if method == Method::POST {
            self.http.post_json_with_headers_and_query(
                &endpoint,
                query,
                &[("X-MBX-APIKEY", self.api_key.clone())],
            )
        } else {
            self.http.get_json_with_headers_and_query(
                &endpoint,
                query,
                &[("X-MBX-APIKEY", self.api_key.clone())],
            )
        }
    }
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

pub trait EquityClient: Send {
    fn exchange_info(&self) -> Result<Value, ExchangeError>;
}

impl EquityClient for BinanceEquityRestClient {
    fn exchange_info(&self) -> Result<Value, ExchangeError> {
        self.exchange_info()
    }
}
