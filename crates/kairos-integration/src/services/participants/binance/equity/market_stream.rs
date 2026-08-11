//! Binance Stocks Trading quote polling market stream.

use crate::application::{IntegrationError, MarketEvent, MarketEventKind};
use crate::domain::ConnectionDescriptor;
use crate::services::transport::{RestPollingMarketStream, RestSnapshotReader};
use kairos_domain_types::{Price, Quantity, Symbol};

use super::client::BinanceEquityRestClient;

pub struct BinanceEquitySnapshotReader {
    client: BinanceEquityRestClient,
}

impl BinanceEquitySnapshotReader {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        BinanceEquityRestClient::with_base_url(api_key, secret, endpoint)
            .map(|client| Self { client })
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))
    }
}

impl RestSnapshotReader for BinanceEquitySnapshotReader {
    fn snapshot(&mut self, symbols: &[String]) -> Result<Vec<MarketEvent>, IntegrationError> {
        symbols
            .iter()
            .map(|symbol| {
                let payload = self
                    .client
                    .latest_quote(symbol)
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                let symbol = payload
                    .get("symbol")
                    .and_then(|value| value.as_str())
                    .filter(|value| !value.trim().is_empty())
                    .unwrap_or(symbol)
                    .to_ascii_uppercase();
                let bid_price = string(&payload, "bidPrice");
                let ask_price = string(&payload, "askPrice");
                if bid_price.is_none() && ask_price.is_none() {
                    return Err(IntegrationError::InvalidPayload(
                        "Binance Equity quote has neither bidPrice nor askPrice".into(),
                    ));
                }
                Ok(MarketEvent {
                    symbol: Symbol::new(symbol)
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    kind: MarketEventKind::Quote,
                    price: parse_optional::<Price>(
                        bid_price.clone().or_else(|| ask_price.clone()),
                    )?,
                    quantity: parse_optional::<Quantity>(string(&payload, "bidSize"))?,
                    rate: None,
                    ask_price: parse_optional::<Price>(ask_price)?,
                    ask_quantity: parse_optional::<Quantity>(string(&payload, "askSize"))?,
                    bids: Vec::new(),
                    asks: Vec::new(),
                    bar: None,
                    greeks: None,
                    first_sequence: None,
                    last_sequence: None,
                    sequence: None,
                    observed_at_unix_nanos: now_unix_nanos().into(),
                })
            })
            .collect()
    }
}

pub type BinanceEquityRestMarketStream = RestPollingMarketStream<BinanceEquitySnapshotReader>;

pub fn rest_market_stream(
    api_key: impl Into<String>,
    secret: impl Into<String>,
    endpoint: impl Into<String>,
) -> Result<BinanceEquityRestMarketStream, IntegrationError> {
    let identity = ConnectionDescriptor::new(
        "market.binance.equity.rest-stream",
        crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Exchange, "binance")
            .expect("static Binance participant"),
        "equity",
    )
    .map_err(IntegrationError::InvalidRequest)?;
    Ok(RestPollingMarketStream::new(
        identity,
        BinanceEquitySnapshotReader::new(api_key, secret, endpoint)?,
    ))
}

fn string(payload: &serde_json::Value, key: &str) -> Option<String> {
    payload
        .get(key)
        .and_then(|value| value.as_str())
        .map(str::to_owned)
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}

fn parse_optional<T>(value: Option<String>) -> Result<Option<T>, IntegrationError>
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
