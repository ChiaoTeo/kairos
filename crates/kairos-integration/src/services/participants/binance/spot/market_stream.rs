//! Binance Spot market stream facade.
//!
//! Binance's production websocket can later implement the same application
//! protocol.  This implementation deliberately uses REST polling so it is
//! useful before the websocket adapter exists and remains deterministic in
//! integration tests.

use kairos_domain_types::{Price, Quantity, Sequence, Symbol};
use serde_json::Value;

use crate::application::capabilities::{
    ConnectionDescriptor, MarketBar, MarketDataKind, MarketStreamCapabilities,
};
use crate::application::{
    HistoricalMarketDataConnection, HistoricalMarketRequest, IntegrationError, MarketEvent,
    MarketEventKind,
};
use crate::services::transport::http::PublicHttpClient;
use crate::services::transport::{RestPollingMarketStream, RestSnapshotReader};

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

    fn historical_bars(
        &mut self,
        request: &HistoricalMarketRequest,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        request.validate()?;
        if !matches!(
            request.data_kind,
            MarketDataKind::Bar | MarketDataKind::TradeBar
        ) {
            return Err(IntegrationError::UnsupportedOperation);
        }
        let interval = request.interval.as_deref().unwrap_or("1m").to_owned();
        let mut start = request.start_time_unix_nanos.get() / 1_000_000;
        let mut result = Vec::new();
        let end = request.end_time_unix_nanos.get() / 1_000_000;
        while start <= end {
            let payload = self
                .http
                .get_json_with_query(
                    &format!("{}/api/v3/klines", self.endpoint),
                    &[
                        ("symbol", request.symbol.to_ascii_uppercase()),
                        ("interval", interval.clone()),
                        ("startTime", start.to_string()),
                        ("endTime", end.to_string()),
                        ("limit", "1000".into()),
                    ],
                )
                .map_err(|error| IntegrationError::Transport(error.to_string()))?;
            let rows = payload.as_array().ok_or_else(|| {
                IntegrationError::InvalidPayload("Binance klines response is not an array".into())
            })?;
            if rows.is_empty() {
                break;
            }
            let mut last_open = start;
            for row in rows {
                let values = row.as_array().ok_or_else(|| {
                    IntegrationError::InvalidPayload("Binance kline row is not an array".into())
                })?;
                let open_time = values
                    .first()
                    .and_then(serde_json::Value::as_i64)
                    .ok_or_else(|| {
                        IntegrationError::InvalidPayload(
                            "Binance kline open time is missing".into(),
                        )
                    })?;
                let open_time = u64::try_from(open_time).map_err(|_| {
                    IntegrationError::InvalidPayload("Binance kline open time is negative".into())
                })?;
                last_open = last_open.max(open_time);
                result.push(MarketEvent {
                    symbol: Symbol::new(request.symbol.to_ascii_uppercase())
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    kind: MarketEventKind::Bar,
                    price: None,
                    quantity: None,
                    rate: None,
                    ask_price: None,
                    ask_quantity: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                    bar: Some(MarketBar {
                        timeframe: interval.clone(),
                        open: string_field(values, 1, "open")?
                            .parse::<Price>()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        high: string_field(values, 2, "high")?
                            .parse::<Price>()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        low: string_field(values, 3, "low")?
                            .parse::<Price>()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        close: string_field(values, 4, "close")?
                            .parse::<Price>()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        volume: values
                            .get(5)
                            .and_then(serde_json::Value::as_str)
                            .map(str::parse::<Quantity>)
                            .transpose()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        derivation: "binance-kline".into(),
                    }),
                    greeks: None,
                    first_sequence: values
                        .get(6)
                        .and_then(serde_json::Value::as_u64)
                        .map(Sequence::new),
                    last_sequence: values
                        .get(7)
                        .and_then(serde_json::Value::as_u64)
                        .map(Sequence::new),
                    sequence: values
                        .get(8)
                        .and_then(serde_json::Value::as_u64)
                        .map(Sequence::new),
                    observed_at_unix_nanos: (open_time * 1_000_000).into(),
                });
            }
            if last_open <= start {
                break;
            }
            start = last_open + 1;
            if rows.len() < 1000 {
                break;
            }
        }
        Ok(result)
    }
}

fn string_field(
    values: &[serde_json::Value],
    index: usize,
    name: &str,
) -> Result<String, IntegrationError> {
    values
        .get(index)
        .and_then(serde_json::Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Binance kline {name} is missing")))
}

impl HistoricalMarketDataConnection for BinanceSpotSnapshotReader {
    fn capabilities(&self) -> MarketStreamCapabilities {
        MarketStreamCapabilities {
            historical: [MarketDataKind::Bar, MarketDataKind::TradeBar]
                .into_iter()
                .collect(),
            ..Default::default()
        }
    }

    fn fetch(
        &mut self,
        request: &HistoricalMarketRequest,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        self.historical_bars(request)
    }
}

impl RestSnapshotReader for BinanceSpotSnapshotReader {
    fn capabilities(&self) -> MarketStreamCapabilities {
        MarketStreamCapabilities {
            realtime: [MarketDataKind::Quote, MarketDataKind::Bar]
                .into_iter()
                .collect(),
            ..Default::default()
        }
    }

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
                let quote =
                    MarketEvent {
                        symbol: Symbol::new(symbol.clone())
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        kind: MarketEventKind::Quote,
                        price: Some(price.parse::<Price>().map_err(|error| {
                            IntegrationError::InvalidPayload(error.to_string())
                        })?),
                        quantity: None,
                        rate: None,
                        ask_price: None,
                        ask_quantity: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                        bar: None,
                        greeks: None,
                        first_sequence: None,
                        last_sequence: None,
                        sequence: None,
                        observed_at_unix_nanos: now_unix_nanos().into(),
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
                    symbol: Symbol::new(symbol.clone())
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    kind: MarketEventKind::Bar,
                    price: None,
                    quantity: None,
                    rate: None,
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
                            .parse::<Price>()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        high: values
                            .get(2)
                            .and_then(Value::as_str)
                            .ok_or_else(|| {
                                IntegrationError::InvalidPayload(
                                    "Binance kline high is missing".into(),
                                )
                            })?
                            .parse::<Price>()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        low: values
                            .get(3)
                            .and_then(Value::as_str)
                            .ok_or_else(|| {
                                IntegrationError::InvalidPayload(
                                    "Binance kline low is missing".into(),
                                )
                            })?
                            .parse::<Price>()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        close: values
                            .get(4)
                            .and_then(Value::as_str)
                            .ok_or_else(|| {
                                IntegrationError::InvalidPayload(
                                    "Binance kline close is missing".into(),
                                )
                            })?
                            .parse::<Price>()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        volume: values
                            .get(5)
                            .and_then(Value::as_str)
                            .map(str::parse::<Quantity>)
                            .transpose()
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                        derivation: "binance-kline".into(),
                    }),
                    greeks: None,
                    first_sequence: None,
                    last_sequence: None,
                    sequence: None,
                    observed_at_unix_nanos: now_unix_nanos().into(),
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
    let identity = ConnectionDescriptor::new(
        "market.binance.spot.rest-stream",
        crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Exchange, "binance")
            .expect("static Binance participant"),
        "spot",
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
