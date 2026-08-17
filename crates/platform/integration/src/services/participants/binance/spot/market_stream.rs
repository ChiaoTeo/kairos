//! Binance Spot bounded historical kline readers.
//!
//! Live market data is implemented by the separate native async WebSocket
//! capability; these REST readers never model a session or polling worker.

use crate::application::capabilities::{MarketBar, MarketDataKind, MarketStreamCapabilities};
use crate::application::{
    AsyncHistoricalMarketDataConnection, HistoricalMarketDataConnection, HistoricalMarketRequest,
    IntegrationError, MarketEvent, MarketEventKind,
};
use crate::services::transport::http::{AsyncPublicHttpClient, PublicHttpClient};
use kairos_primitives::{Price, Quantity, Sequence, Symbol};

pub struct BinanceSpotSnapshotReader {
    http: PublicHttpClient,
    endpoint: String,
}

pub struct BinanceSpotAsyncHistoricalReader {
    http: AsyncPublicHttpClient,
    endpoint: String,
}

fn normalize_endpoint(value: impl Into<String>) -> Result<String, IntegrationError> {
    let endpoint = value.into().trim_end_matches('/').to_string();
    if endpoint.is_empty() {
        return Err(IntegrationError::InvalidRequest(
            "Binance Spot endpoint is required".into(),
        ));
    }
    Ok(endpoint)
}

fn capabilities() -> MarketStreamCapabilities {
    MarketStreamCapabilities {
        historical: [MarketDataKind::Bar, MarketDataKind::TradeBar]
            .into_iter()
            .collect(),
        ..Default::default()
    }
}

impl BinanceSpotSnapshotReader {
    pub fn new(endpoint: impl Into<String>) -> Result<Self, IntegrationError> {
        let endpoint = normalize_endpoint(endpoint)?;
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
            let (mut events, last_open, row_count) =
                normalize_page(request, &interval, start, payload)?;
            result.append(&mut events);
            if row_count == 0 || last_open <= start || row_count < 1000 {
                break;
            }
            start = last_open + 1;
        }
        Ok(result)
    }
}

impl BinanceSpotAsyncHistoricalReader {
    pub fn new(endpoint: impl Into<String>) -> Result<Self, IntegrationError> {
        Ok(Self {
            endpoint: normalize_endpoint(endpoint)?,
            http: AsyncPublicHttpClient::new("kairos-integration/binance-spot-async-historical")
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
        })
    }

    async fn historical_bars(
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
        let end = request.end_time_unix_nanos.get() / 1_000_000;
        let mut result = Vec::new();
        while start <= end {
            let payload = self
                .http
                .get_json_response_with_headers_and_query(
                    &format!("{}/api/v3/klines", self.endpoint),
                    &[
                        ("symbol", request.symbol.to_ascii_uppercase()),
                        ("interval", interval.clone()),
                        ("startTime", start.to_string()),
                        ("endTime", end.to_string()),
                        ("limit", "1000".into()),
                    ],
                    &[],
                )
                .await
                .map_err(|error| IntegrationError::Transport(error.to_string()))?
                .body;
            let (mut events, last_open, row_count) =
                normalize_page(request, &interval, start, payload)?;
            result.append(&mut events);
            if row_count == 0 || last_open <= start || row_count < 1000 {
                break;
            }
            start = last_open + 1;
        }
        Ok(result)
    }
}

fn normalize_page(
    request: &HistoricalMarketRequest,
    interval: &str,
    start: u64,
    payload: serde_json::Value,
) -> Result<(Vec<MarketEvent>, u64, usize), IntegrationError> {
    let rows = payload.as_array().ok_or_else(|| {
        IntegrationError::InvalidPayload("Binance klines response is not an array".into())
    })?;
    let mut last_open = start;
    let mut events = Vec::with_capacity(rows.len());
    for row in rows {
        let values = row.as_array().ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance kline row is not an array".into())
        })?;
        let open_time = values
            .first()
            .and_then(serde_json::Value::as_i64)
            .ok_or_else(|| {
                IntegrationError::InvalidPayload("Binance kline open time is missing".into())
            })?;
        let open_time = u64::try_from(open_time).map_err(|_| {
            IntegrationError::InvalidPayload("Binance kline open time is negative".into())
        })?;
        last_open = last_open.max(open_time);
        events.push(normalize_bar(request, interval, values, open_time)?);
    }
    Ok((events, last_open, rows.len()))
}

fn normalize_bar(
    request: &HistoricalMarketRequest,
    interval: &str,
    values: &[serde_json::Value],
    open_time: u64,
) -> Result<MarketEvent, IntegrationError> {
    Ok(MarketEvent {
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
            timeframe: interval.to_owned(),
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
        venue: Default::default(),
    })
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
        capabilities()
    }

    fn fetch(
        &mut self,
        request: &HistoricalMarketRequest,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        self.historical_bars(request)
    }
}

impl AsyncHistoricalMarketDataConnection for BinanceSpotAsyncHistoricalReader {
    fn capabilities(&self) -> MarketStreamCapabilities {
        capabilities()
    }

    async fn fetch(
        &mut self,
        request: &HistoricalMarketRequest,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        self.historical_bars(request).await
    }
}

#[cfg(test)]
mod tests {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    use crate::application::{
        AsyncHistoricalMarketDataConnection, HistoricalMarketRequest, MarketDataKind,
    };

    use super::BinanceSpotAsyncHistoricalReader;

    #[tokio::test(flavor = "current_thread")]
    async fn async_historical_bars_use_the_callers_runtime() {
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = [0_u8; 4_096];
            let read = stream.read(&mut request).await.unwrap();
            let request = String::from_utf8_lossy(&request[..read]);
            assert!(request.starts_with("GET /api/v3/klines?"), "{request}");
            assert!(request.contains("symbol=BTCUSDT"), "{request}");
            assert!(request.contains("interval=1m"), "{request}");
            let body = r#"[[1700000,"1","2","0.5","1.5","10",1759999,3,4]]"#;
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
        });
        let mut connection =
            BinanceSpotAsyncHistoricalReader::new(format!("http://{address}")).unwrap();
        let events = connection
            .fetch(&HistoricalMarketRequest {
                symbol: kairos_primitives::Symbol::new("BTCUSDT").unwrap(),
                data_kind: MarketDataKind::Bar,
                start_time_unix_nanos: kairos_primitives::UnixNanos::new(1_700_000_000_000),
                end_time_unix_nanos: kairos_primitives::UnixNanos::new(1_800_000_000_000),
                interval: Some("1m".into()),
                adjusted: Some(false),
            })
            .await
            .unwrap();
        server.await.unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].observed_at_unix_nanos.get(), 1_700_000_000_000);
        assert_eq!(events[0].bar.as_ref().unwrap().close.to_string(), "1.5");
    }
}
