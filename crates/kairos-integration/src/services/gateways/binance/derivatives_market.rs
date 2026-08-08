//! Binance Futures and Options public quote polling market streams.

use crate::application::{IntegrationError, MarketEvent, MarketEventKind};
use crate::domain::{
    AccessScope, ConnectionIdentity, IntegrationCapability, ProductFamily, TransportKind,
};
use crate::services::drivers::http::PublicHttpClient;
use crate::services::streams::{RestPollingMarketStream, RestSnapshotReader};
use serde_json::Value;

pub struct BinanceDerivativesSnapshotReader {
    http: PublicHttpClient,
    endpoint: String,
    path: String,
    product: ProductFamily,
}

impl BinanceDerivativesSnapshotReader {
    pub fn new(
        endpoint: impl Into<String>,
        path: impl Into<String>,
        product: ProductFamily,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::UsdMFutures | ProductFamily::CoinMFutures | ProductFamily::Options
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance derivatives market requires futures or options product".into(),
            ));
        }
        let endpoint = endpoint.into().trim_end_matches('/').to_string();
        let path = path.into();
        if endpoint.is_empty() || path.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance derivatives market endpoint is required".into(),
            ));
        }
        Ok(Self {
            http: PublicHttpClient::new("kairos-integration/binance-derivatives-market")
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
            endpoint,
            path,
            product,
        })
    }
}

impl RestSnapshotReader for BinanceDerivativesSnapshotReader {
    fn snapshot(&mut self, symbols: &[String]) -> Result<Vec<MarketEvent>, IntegrationError> {
        symbols
            .iter()
            .map(|symbol| {
                let payload = self
                    .http
                    .get_json_with_query(
                        &format!("{}{}", self.endpoint, self.path),
                        &[("symbol", symbol.clone())],
                    )
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                let value = if self.product == ProductFamily::Options {
                    payload
                        .as_array()
                        .and_then(|values| values.first())
                        .cloned()
                        .unwrap_or(payload)
                } else {
                    payload
                };
                quote_event(&value, symbol)
            })
            .collect()
    }
}

pub type BinanceDerivativesRestMarketStream =
    RestPollingMarketStream<BinanceDerivativesSnapshotReader>;

pub fn rest_market_stream(
    endpoint: impl Into<String>,
    path: impl Into<String>,
    product: ProductFamily,
) -> Result<BinanceDerivativesRestMarketStream, IntegrationError> {
    let product_name = match product {
        ProductFamily::UsdMFutures => "usd-m-futures",
        ProductFamily::CoinMFutures => "coin-m-futures",
        ProductFamily::Options => "options",
        _ => "derivatives",
    };
    let identity = ConnectionIdentity::new(
        format!("market.binance.{product_name}.rest-stream"),
        crate::domain::IntegrationRoute::exchange("binance"),
        Some(product),
        AccessScope::Public,
        TransportKind::Rest,
        IntegrationCapability::MarketStream,
    )
    .map_err(IntegrationError::InvalidRequest)?;
    Ok(RestPollingMarketStream::new(
        identity,
        BinanceDerivativesSnapshotReader::new(endpoint, path, product)?,
    ))
}

fn quote_event(payload: &Value, fallback_symbol: &str) -> Result<MarketEvent, IntegrationError> {
    let symbol = payload
        .get("symbol")
        .or_else(|| payload.get("s"))
        .and_then(Value::as_str)
        .unwrap_or(fallback_symbol)
        .to_ascii_uppercase();
    let bid = text(payload, &["bidPrice", "b"]);
    let ask = text(payload, &["askPrice", "a"]);
    if bid.is_none() && ask.is_none() {
        return Err(IntegrationError::InvalidPayload(
            "Binance derivatives quote has neither bid nor ask".into(),
        ));
    }
    Ok(MarketEvent {
        symbol,
        kind: MarketEventKind::Quote,
        price: bid.clone().or_else(|| ask.clone()),
        quantity: text(payload, &["bidQty", "B"]),
        ask_price: ask,
        ask_quantity: text(payload, &["askQty", "A"]),
        bids: Vec::new(),
        asks: Vec::new(),
        first_sequence: None,
        last_sequence: None,
        sequence: None,
        observed_at_unix_nanos: now_unix_nanos(),
    })
}

fn text(payload: &Value, keys: &[&str]) -> Option<String> {
    keys.iter()
        .find_map(|key| payload.get(*key).and_then(Value::as_str).map(str::to_owned))
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}
