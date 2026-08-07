//! Binance Spot market stream facade.
//!
//! Binance's production websocket can later implement the same application
//! protocol.  This implementation deliberately uses REST polling so it is
//! useful before the websocket adapter exists and remains deterministic in
//! integration tests.

use crate::application::{IntegrationError, MarketEvent, MarketEventKind};
use crate::domain::{
    AccessScope, ConnectionIdentity, IntegrationCapability, ProductFamily, TransportKind,
};
use crate::services::drivers::http::PublicHttpClient;
use crate::services::streams::{RestPollingMarketStream, RestSnapshotReader};

pub struct BinanceSpotSnapshotReader {
    http: PublicHttpClient,
    endpoint: String,
}

impl BinanceSpotSnapshotReader {
    pub fn new(endpoint: impl Into<String>) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into().trim_end_matches('/').to_string();
        if endpoint.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot endpoint is required".into(),
            ));
        }
        let http = PublicHttpClient::new("kairos-integration/binance-spot-market-stream")
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        Ok(Self { http, endpoint })
    }
}

impl RestSnapshotReader for BinanceSpotSnapshotReader {
    fn snapshot(&mut self, symbols: &[String]) -> Result<Vec<MarketEvent>, IntegrationError> {
        symbols
            .iter()
            .map(|symbol| {
                let payload = self
                    .http
                    .get_json_with_query(
                        &format!("{}/api/v3/ticker/price", self.endpoint),
                        &[("symbol", symbol.clone())],
                    )
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                let price = payload
                    .get("price")
                    .and_then(|value| value.as_str())
                    .ok_or_else(|| {
                        IntegrationError::InvalidPayload(
                            "Binance ticker response has no price".into(),
                        )
                    })?;
                Ok(MarketEvent {
                    symbol: symbol.clone(),
                    kind: MarketEventKind::Quote,
                    price: Some(price.to_string()),
                    quantity: None,
                    ask_price: None,
                    ask_quantity: None,
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

pub type BinanceSpotRestMarketStream = RestPollingMarketStream<BinanceSpotSnapshotReader>;

pub fn rest_market_stream(
    endpoint: impl Into<String>,
) -> Result<BinanceSpotRestMarketStream, IntegrationError> {
    let identity = ConnectionIdentity::new(
        "market.binance.spot.rest-stream",
        "binance",
        Some(ProductFamily::Spot),
        AccessScope::Public,
        TransportKind::Rest,
        IntegrationCapability::MarketStream,
    )
    .map_err(IntegrationError::InvalidRequest)?;
    Ok(RestPollingMarketStream::new(
        identity,
        BinanceSpotSnapshotReader::new(endpoint)?,
    ))
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}
