//! OKX public market quote polling streams for spot, swap, futures and options.

use crate::application::{IntegrationError, MarketEvent, MarketEventKind};
use crate::domain::{
    AccessScope, ConnectionIdentity, IntegrationCapability, ProductFamily, TransportKind,
};
use crate::services::drivers::http::PublicHttpClient;
use crate::services::streams::{RestPollingMarketStream, RestSnapshotReader};
use serde_json::Value;

pub struct OkxSnapshotReader {
    http: PublicHttpClient,
    endpoint: String,
}

impl OkxSnapshotReader {
    pub fn new(
        endpoint: impl Into<String>,
        product: ProductFamily,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::Spot
                | ProductFamily::UsdMFutures
                | ProductFamily::CoinMFutures
                | ProductFamily::Options
        ) {
            return Err(IntegrationError::InvalidRequest(
                "OKX market stream requires spot, futures, or options product".into(),
            ));
        }
        let endpoint = endpoint.into().trim_end_matches('/').to_string();
        if endpoint.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "OKX market endpoint is required".into(),
            ));
        }
        Ok(Self {
            http: PublicHttpClient::new("kairos-integration/okx-market-stream")
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
            endpoint,
        })
    }
}

impl RestSnapshotReader for OkxSnapshotReader {
    fn snapshot(&mut self, symbols: &[String]) -> Result<Vec<MarketEvent>, IntegrationError> {
        symbols
            .iter()
            .map(|symbol| {
                let payload = self
                    .http
                    .get_json_with_query(
                        &format!("{}/api/v5/market/ticker", self.endpoint),
                        &[("instId", symbol.clone())],
                    )
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                let row = payload
                    .get("data")
                    .and_then(Value::as_array)
                    .and_then(|rows| rows.first())
                    .ok_or_else(|| {
                        IntegrationError::InvalidPayload("OKX ticker response has no data".into())
                    })?;
                let bid = text(row, "bidPx");
                let ask = text(row, "askPx");
                if bid.is_none() && ask.is_none() {
                    return Err(IntegrationError::InvalidPayload(
                        "OKX ticker has neither bid nor ask".into(),
                    ));
                }
                Ok(MarketEvent {
                    symbol: text(row, "instId").unwrap_or_else(|| symbol.clone()),
                    kind: MarketEventKind::Quote,
                    price: bid.clone().or_else(|| ask.clone()),
                    quantity: text(row, "bidSz"),
                    ask_price: ask,
                    ask_quantity: text(row, "askSz"),
                    bids: Vec::new(),
                    asks: Vec::new(),
                    first_sequence: None,
                    last_sequence: None,
                    sequence: None,
                    observed_at_unix_nanos: now_unix_nanos(),
                })
            })
            .collect()
    }
}

pub type OkxRestMarketStream = RestPollingMarketStream<OkxSnapshotReader>;

pub fn rest_market_stream(
    endpoint: impl Into<String>,
    product: ProductFamily,
) -> Result<OkxRestMarketStream, IntegrationError> {
    let product_name = match product {
        ProductFamily::Spot => "spot",
        ProductFamily::UsdMFutures => "swap",
        ProductFamily::CoinMFutures => "futures",
        ProductFamily::Options => "options",
        _ => "market",
    };
    let identity = ConnectionIdentity::new(
        format!("market.okx.{product_name}.rest-stream"),
        crate::domain::IntegrationRoute::exchange("okx"),
        Some(product),
        AccessScope::Public,
        TransportKind::Rest,
        IntegrationCapability::MarketStream,
    )
    .map_err(IntegrationError::InvalidRequest)?;
    Ok(RestPollingMarketStream::new(
        identity,
        OkxSnapshotReader::new(endpoint, product)?,
    ))
}

fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
}
fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}
