//! Binance async REST market snapshots.
//!
//! These are stateless provider operations. Market owns subscription intent
//! and polling cadence; Integration only performs provider I/O and maps the
//! response into Kairos facts.

use std::collections::{HashMap, HashSet};
use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::{Price, ProviderSymbol, Quantity, Rate, Symbol, UnixNanos};
use serde_json::Value;

use crate::application::capabilities::market_facts::MarketGreeks;
use crate::application::{
    AsyncMarketSnapshotConnection, IntegrationError, MarketEvent, MarketEventKind,
};
use crate::services::transport::http::{AsyncPublicHttpClient, ExchangeError};

use super::ConnectionDomain;

pub struct BinanceAsyncMarketSnapshot {
    client: AsyncPublicHttpClient,
    endpoint: String,
    kind: SnapshotKind,
}

enum SnapshotKind {
    Spot,
    Derivatives {
        product: ConnectionDomain,
        path: String,
    },
}

impl BinanceAsyncMarketSnapshot {
    fn new(endpoint: impl Into<String>, kind: SnapshotKind) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into().trim_end_matches('/').to_owned();
        if endpoint.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance market snapshot endpoint is required".into(),
            ));
        }
        Ok(Self {
            client: AsyncPublicHttpClient::new("kairos-integration/binance-market-snapshot")
                .map_err(map_http_error)?,
            endpoint,
            kind,
        })
    }
}

pub fn spot_snapshot(
    endpoint: impl Into<String>,
) -> Result<BinanceAsyncMarketSnapshot, IntegrationError> {
    BinanceAsyncMarketSnapshot::new(endpoint, SnapshotKind::Spot)
}

pub fn derivatives_snapshot(
    product: ConnectionDomain,
    endpoint: impl Into<String>,
    path: impl Into<String>,
) -> Result<BinanceAsyncMarketSnapshot, IntegrationError> {
    if !matches!(
        product,
        ConnectionDomain::UsdMFutures | ConnectionDomain::CoinMFutures | ConnectionDomain::Options
    ) {
        return Err(IntegrationError::InvalidRequest(
            "Binance derivatives snapshot requires futures or options product".into(),
        ));
    }
    let path = path.into();
    if path.trim().is_empty() {
        return Err(IntegrationError::InvalidRequest(
            "Binance derivatives snapshot path is required".into(),
        ));
    }
    BinanceAsyncMarketSnapshot::new(endpoint, SnapshotKind::Derivatives { product, path })
}

impl AsyncMarketSnapshotConnection for BinanceAsyncMarketSnapshot {
    async fn fetch_snapshot(
        &mut self,
        symbols: &[ProviderSymbol],
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        match &self.kind {
            SnapshotKind::Spot => self.fetch_spot(symbols).await,
            SnapshotKind::Derivatives { product, path } => {
                self.fetch_derivatives(symbols, *product, path).await
            }
        }
    }
}

impl BinanceAsyncMarketSnapshot {
    async fn fetch_spot(
        &self,
        symbols: &[ProviderSymbol],
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        let mut events = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let payload = self
                .client
                .get_json_response_with_headers_and_query(
                    &format!("{}/api/v3/ticker/bookTicker", self.endpoint),
                    &[("symbol", symbol.as_str().to_owned())],
                    &[],
                )
                .await
                .map_err(map_http_error)?
                .body;
            events.push(quote_event(
                symbol.as_str(),
                &payload,
                &["bidPrice", "b"],
                &["bidQty", "B"],
                &["askPrice", "a"],
                &["askQty", "A"],
            )?);
        }
        Ok(events)
    }

    async fn fetch_derivatives(
        &self,
        symbols: &[ProviderSymbol],
        product: ConnectionDomain,
        path: &str,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        if product == ConnectionDomain::Options {
            return self.fetch_options(symbols, path).await;
        }
        let mut events = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let payload = self
                .client
                .get_json_response_with_headers_and_query(
                    &format!("{}{}", self.endpoint, path),
                    &[("symbol", symbol.as_str().to_owned())],
                    &[],
                )
                .await
                .map_err(map_http_error)?
                .body;
            let payload = payload
                .as_array()
                .and_then(|values| values.first())
                .cloned()
                .unwrap_or(payload);
            events.push(quote_event(
                symbol.as_str(),
                &payload,
                &["bidPrice", "b"],
                &["bidQty", "B"],
                &["askPrice", "a"],
                &["askQty", "A"],
            )?);
        }
        Ok(events)
    }

    async fn fetch_options(
        &self,
        symbols: &[ProviderSymbol],
        path: &str,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        let requested = symbols
            .iter()
            .map(|symbol| symbol.as_str().to_ascii_uppercase())
            .collect::<HashSet<_>>();
        let tickers = self
            .client
            .get_json_response_with_headers_and_query(
                &format!("{}{}", self.endpoint, path),
                &[],
                &[],
            )
            .await
            .map_err(map_http_error)?
            .body;
        let marks = self
            .client
            .get_json_response_with_headers_and_query(
                &format!("{}/eapi/v1/mark", self.endpoint),
                &[],
                &[],
            )
            .await
            .map_err(map_http_error)?
            .body;
        let marks = rows(&marks)
            .filter_map(|value| {
                provider_symbol(value).map(|symbol| (symbol.to_ascii_uppercase(), value))
            })
            .collect::<HashMap<_, _>>();
        let mut events = Vec::new();
        for ticker in rows(&tickers) {
            let Some(symbol) = provider_symbol(ticker) else {
                continue;
            };
            let symbol = symbol.to_ascii_uppercase();
            if !requested.contains(&symbol) {
                continue;
            }
            events.push(quote_event(
                &symbol,
                ticker,
                &["bidPrice", "b"],
                &["bidQty", "B"],
                &["askPrice", "a"],
                &["askQty", "A"],
            )?);
            if let Some(mark) = marks.get(&symbol) {
                if let Some(event) = greeks_event(&symbol, mark) {
                    events.push(event);
                }
            }
        }
        Ok(events)
    }
}

fn quote_event(
    fallback_symbol: &str,
    payload: &Value,
    bid_keys: &[&str],
    bid_quantity_keys: &[&str],
    ask_keys: &[&str],
    ask_quantity_keys: &[&str],
) -> Result<MarketEvent, IntegrationError> {
    let symbol = provider_symbol(payload)
        .unwrap_or(fallback_symbol)
        .to_ascii_uppercase();
    let bid = text(payload, bid_keys);
    let ask = text(payload, ask_keys);
    if bid.is_none() && ask.is_none() {
        return Err(IntegrationError::InvalidPayload(format!(
            "Binance quote for {symbol} has neither bid nor ask"
        )));
    }
    Ok(MarketEvent {
        symbol: Symbol::new(symbol)
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        kind: MarketEventKind::Snapshot,
        price: decimal::<Price>(bid.clone().or_else(|| ask.clone()))?,
        quantity: decimal::<Quantity>(text(payload, bid_quantity_keys))?,
        rate: None,
        ask_price: decimal::<Price>(ask)?,
        ask_quantity: decimal::<Quantity>(text(payload, ask_quantity_keys))?,
        bids: Vec::new(),
        asks: Vec::new(),
        bar: None,
        greeks: None,
        first_sequence: None,
        last_sequence: None,
        sequence: payload.get("u").and_then(Value::as_u64).map(Into::into),
        observed_at_unix_nanos: now_unix_nanos(),
    })
}

fn greeks_event(symbol: &str, payload: &Value) -> Option<MarketEvent> {
    let delta = text(payload, &["delta"]);
    let gamma = text(payload, &["gamma"]);
    let vega = text(payload, &["vega"]);
    let theta = text(payload, &["theta"]);
    let implied_volatility = text(payload, &["markIV", "sigma", "impliedVolatility"]);
    if delta.is_none()
        && gamma.is_none()
        && vega.is_none()
        && theta.is_none()
        && implied_volatility.is_none()
    {
        return None;
    }
    Some(MarketEvent {
        symbol: Symbol::new(symbol).ok()?,
        kind: MarketEventKind::Greeks,
        price: None,
        quantity: None,
        rate: None,
        ask_price: None,
        ask_quantity: None,
        bids: Vec::new(),
        asks: Vec::new(),
        bar: None,
        greeks: Some(MarketGreeks {
            expiry_unix_nanos: None,
            strike: symbol
                .split('-')
                .nth(2)
                .and_then(|value| value.parse::<Price>().ok()),
            delta: delta.and_then(|value| value.parse::<Rate>().ok()),
            gamma: gamma.and_then(|value| value.parse::<Rate>().ok()),
            vega: vega.and_then(|value| value.parse::<Rate>().ok()),
            theta: theta.and_then(|value| value.parse::<Rate>().ok()),
            implied_volatility: implied_volatility.and_then(|value| value.parse::<Rate>().ok()),
            derivation: "binance-options-mark".into(),
        }),
        first_sequence: None,
        last_sequence: None,
        sequence: None,
        observed_at_unix_nanos: now_unix_nanos(),
    })
}

fn rows(payload: &Value) -> impl Iterator<Item = &Value> {
    payload.as_array().into_iter().flatten()
}

fn provider_symbol(payload: &Value) -> Option<&str> {
    payload
        .get("symbol")
        .or_else(|| payload.get("s"))
        .and_then(Value::as_str)
}

fn text(payload: &Value, keys: &[&str]) -> Option<String> {
    keys.iter()
        .find_map(|key| payload.get(*key).and_then(Value::as_str).map(str::to_owned))
}

fn decimal<T>(value: Option<String>) -> Result<Option<T>, IntegrationError>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    value
        .map(|value| {
            value
                .parse::<T>()
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
        })
        .transpose()
}

fn now_unix_nanos() -> UnixNanos {
    UnixNanos::new(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .min(u128::from(u64::MAX)) as u64,
    )
}

fn map_http_error(error: ExchangeError) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::LocalRateLimit { message, .. } => IntegrationError::RateLimited(message),
        ExchangeError::Http { status: 401, body } => IntegrationError::Authentication(body),
        ExchangeError::Http { status: 403, body } => IntegrationError::Authorization(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::spot_snapshot;
    use crate::application::AsyncMarketSnapshotConnection;
    use kairos_primitives::ProviderSymbol;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    async fn server(body: &'static str) -> (String, tokio::task::JoinHandle<String>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let task = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = vec![0_u8; 4_096];
            let read = stream.read(&mut request).await.unwrap();
            let request = String::from_utf8_lossy(&request[..read]).to_string();
            stream
                .write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                        body.len()
                    )
                    .as_bytes(),
                )
                .await
                .unwrap();
            request
        });
        (endpoint, task)
    }

    #[tokio::test]
    async fn spot_snapshot_runs_on_the_caller_runtime() {
        let (endpoint, request) = server(
            r#"{"symbol":"BTCUSDT","bidPrice":"100","bidQty":"2","askPrice":"101","askQty":"3"}"#,
        )
        .await;
        let mut connection = spot_snapshot(endpoint).unwrap();
        let events = connection
            .fetch_snapshot(&[ProviderSymbol::new("BTCUSDT").unwrap()])
            .await
            .unwrap();

        assert_eq!(events.len(), 1);
        assert_eq!(events[0].price.unwrap().to_string(), "100");
        assert_eq!(events[0].ask_price.unwrap().to_string(), "101");
        assert!(request
            .await
            .unwrap()
            .contains("/api/v3/ticker/bookTicker?symbol=BTCUSDT"));
    }
}
