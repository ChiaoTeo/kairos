//! Binance Spot market stream facade.
//!
//! Binance's production websocket can later implement the same application
//! protocol.  This implementation deliberately uses REST polling so it is
//! useful before the websocket adapter exists and remains deterministic in
//! integration tests.

use serde_json::Value;

use crate::application::{IntegrationError, MarketEvent, MarketEventKind};
use crate::domain::{
    AccessScope, ConnectionIdentity, IntegrationCapability, MarketBar, ProductFamily, TransportKind,
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
            .map(|symbol| -> Result<Vec<MarketEvent>, IntegrationError> {
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
                let quote = MarketEvent {
                    symbol: symbol.clone(),
                    kind: MarketEventKind::Quote,
                    price: Some(price.to_string()),
                    quantity: None,
                    ask_price: None,
                    ask_quantity: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                    bar: None,
                    greeks: None,
                    first_sequence: None,
                    last_sequence: None,
                    sequence: None,
                    observed_at_unix_nanos: now_unix_nanos(),
                };
                let bar_payload = self
                    .http
                    .get_json_with_query(
                        &format!("{}/api/v3/klines", self.endpoint),
                        &[
                            ("symbol", symbol.clone()),
                            ("interval", "1m".into()),
                            ("limit", "1".into()),
                        ],
                    )
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                let values = bar_payload
                    .as_array()
                    .and_then(|values| values.first())
                    .and_then(Value::as_array)
                    .ok_or_else(|| {
                        IntegrationError::InvalidPayload(
                            "Binance kline response has no candle".into(),
                        )
                    })?;
                let bar = MarketEvent {
                    symbol: symbol.clone(),
                    kind: MarketEventKind::Bar,
                    price: None,
                    quantity: None,
                    ask_price: None,
                    ask_quantity: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                    bar: Some(MarketBar {
                        timeframe: "1m".into(),
                        open: values
                            .get(1)
                            .and_then(Value::as_str)
                            .ok_or_else(|| {
                                IntegrationError::InvalidPayload(
                                    "Binance kline open is missing".into(),
                                )
                            })?
                            .into(),
                        high: values
                            .get(2)
                            .and_then(Value::as_str)
                            .ok_or_else(|| {
                                IntegrationError::InvalidPayload(
                                    "Binance kline high is missing".into(),
                                )
                            })?
                            .into(),
                        low: values
                            .get(3)
                            .and_then(Value::as_str)
                            .ok_or_else(|| {
                                IntegrationError::InvalidPayload(
                                    "Binance kline low is missing".into(),
                                )
                            })?
                            .into(),
                        close: values
                            .get(4)
                            .and_then(Value::as_str)
                            .ok_or_else(|| {
                                IntegrationError::InvalidPayload(
                                    "Binance kline close is missing".into(),
                                )
                            })?
                            .into(),
                        volume: values.get(5).and_then(Value::as_str).map(str::to_owned),
                        derivation: "binance-kline".into(),
                    }),
                    greeks: None,
                    first_sequence: None,
                    last_sequence: None,
                    sequence: None,
                    observed_at_unix_nanos: now_unix_nanos(),
                };
                Ok(vec![quote, bar])
            })
            .collect::<Result<Vec<_>, _>>()
            .map(|events| events.into_iter().flatten().collect())
    }
}

pub type BinanceSpotRestMarketStream = RestPollingMarketStream<BinanceSpotSnapshotReader>;

pub fn rest_market_stream(
    endpoint: impl Into<String>,
) -> Result<BinanceSpotRestMarketStream, IntegrationError> {
    let identity = ConnectionIdentity::new(
        "market.binance.spot.rest-stream",
        crate::domain::IntegrationRoute::exchange("binance"),
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
