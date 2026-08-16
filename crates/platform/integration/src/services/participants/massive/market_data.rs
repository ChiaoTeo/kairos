//! Massive Stocks and Options WebSocket market streams.

use kairos_domain_types::{Price, Quantity, Sequence, Symbol};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use tokio_tungstenite::tungstenite::Message;

use crate::application::capabilities::{
    ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState, MarketBar,
    MarketDataKind, MarketStreamCapabilities,
};
use crate::application::{
    AsyncHistoricalMarketDataConnection, AsyncMarketEventSource, HistoricalMarketDataConnection,
    HistoricalMarketRequest, IntegrationError, MarketEvent, MarketEventKind, MarketSubscription,
    SubscriptionId,
};
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};

use crate::services::participants::massive::{MassiveAsyncRestClient, MassiveStocksRestClient};

/// Massive-native market family. This private service type deliberately does
/// not reuse the legacy cross-participant product taxonomy.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum MarketType {
    Equity,
    Option,
}

pub(crate) struct MassiveHistoricalMarketData {
    client: MassiveStocksRestClient,
    market_type: MarketType,
}

pub(crate) struct MassiveAsyncMarketStream {
    state: ConnectionState,
    api_key: String,
    endpoint: String,
    socket: Option<AsyncTokioSocket>,
    subscriptions: BTreeMap<SubscriptionId, Vec<String>>,
    next_subscription_id: u64,
    event_capacity: usize,
}

pub(crate) struct MassiveAsyncHistoricalMarketData {
    client: MassiveAsyncRestClient,
    market_type: MarketType,
}

impl MassiveAsyncHistoricalMarketData {
    pub(crate) fn new(
        api_key: impl Into<String>,
        endpoint: impl Into<String>,
        market_type: MarketType,
    ) -> Result<Self, IntegrationError> {
        let client = MassiveAsyncRestClient::with_base_url(api_key, endpoint)
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        let client = match market_type {
            MarketType::Equity => client.for_equity(),
            MarketType::Option => client.for_options(),
        };
        Ok(Self {
            client,
            market_type,
        })
    }
}

impl MassiveHistoricalMarketData {
    pub(crate) fn new(
        api_key: impl Into<String>,
        endpoint: impl Into<String>,
        market_type: MarketType,
    ) -> Result<Self, IntegrationError> {
        let client = MassiveStocksRestClient::with_base_url(api_key, endpoint)
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        let client = match market_type {
            MarketType::Equity => client.for_equity(),
            MarketType::Option => client.for_options(),
        };
        Ok(Self {
            client,
            market_type,
        })
    }
}

impl HistoricalMarketDataConnection for MassiveHistoricalMarketData {
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
        request.validate()?;
        if !matches!(
            request.data_kind,
            MarketDataKind::Bar | MarketDataKind::TradeBar
        ) {
            return Err(IntegrationError::UnsupportedOperation);
        }
        let interval = request.interval.as_deref().unwrap_or("1m");
        let (multiplier, timespan) = parse_interval(interval)?;
        let rows = self
            .client
            .historical_bars(
                request.symbol.as_str(),
                multiplier,
                timespan,
                (request.start_time_unix_nanos.get() / 1_000_000) as i64,
                (request.end_time_unix_nanos.get() / 1_000_000) as i64,
                request.adjusted.unwrap_or(false),
            )
            .map_err(IntegrationError::Transport)?;
        normalize_historical(rows, request, interval, self.market_type)
    }
}

impl AsyncHistoricalMarketDataConnection for MassiveAsyncHistoricalMarketData {
    fn capabilities(&self) -> MarketStreamCapabilities {
        historical_capabilities()
    }

    async fn fetch(
        &mut self,
        request: &HistoricalMarketRequest,
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        request.validate()?;
        match request.data_kind {
            MarketDataKind::Bar | MarketDataKind::TradeBar => {
                let interval = request.interval.as_deref().unwrap_or("1m");
                let (multiplier, timespan) = parse_interval(interval)?;
                let rows = self
                    .client
                    .historical_bars(
                        request.symbol.as_str(),
                        multiplier,
                        timespan,
                        (request.start_time_unix_nanos.get() / 1_000_000) as i64,
                        (request.end_time_unix_nanos.get() / 1_000_000) as i64,
                        request.adjusted.unwrap_or(false),
                    )
                    .await
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                normalize_historical(rows, request, interval, self.market_type)
            }
            MarketDataKind::Quote => {
                let rows = self
                    .client
                    .historical_quotes(
                        request.symbol.as_str(),
                        request.start_time_unix_nanos.get(),
                        request.end_time_unix_nanos.get(),
                    )
                    .await
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                normalize_historical_quotes(rows, request)
            }
            MarketDataKind::Trade => {
                let rows = self
                    .client
                    .historical_trades(
                        request.symbol.as_str(),
                        request.start_time_unix_nanos.get(),
                        request.end_time_unix_nanos.get(),
                    )
                    .await
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                normalize_historical_trades(rows, request)
            }
            _ => Err(IntegrationError::UnsupportedOperation),
        }
    }
}

fn historical_capabilities() -> MarketStreamCapabilities {
    MarketStreamCapabilities {
        historical: [
            MarketDataKind::Bar,
            MarketDataKind::TradeBar,
            MarketDataKind::Quote,
            MarketDataKind::Trade,
        ]
        .into_iter()
        .collect(),
        ..Default::default()
    }
}

fn normalize_historical_quotes(
    rows: Vec<crate::services::participants::massive::connection::MassiveHistoricalQuote>,
    request: &HistoricalMarketRequest,
) -> Result<Vec<MarketEvent>, IntegrationError> {
    rows.into_iter()
        .map(|row| {
            Ok(MarketEvent {
                symbol: Symbol::new(request.symbol.as_str())
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                kind: MarketEventKind::Quote,
                price: parse_optional::<Price>(row.bid_price.or_else(|| row.ask_price.clone()))?,
                quantity: parse_optional::<Quantity>(row.bid_size)?,
                rate: None,
                ask_price: parse_optional::<Price>(row.ask_price)?,
                ask_quantity: parse_optional::<Quantity>(row.ask_size)?,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: None,
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: row.sequence_number.map(Sequence::new),
                observed_at_unix_nanos: row.sip_timestamp_unix_nanos.into(),
            })
        })
        .collect()
}

fn normalize_historical_trades(
    rows: Vec<crate::services::participants::massive::connection::MassiveHistoricalTrade>,
    request: &HistoricalMarketRequest,
) -> Result<Vec<MarketEvent>, IntegrationError> {
    rows.into_iter()
        .map(|row| {
            Ok(MarketEvent {
                symbol: Symbol::new(request.symbol.as_str())
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                kind: MarketEventKind::Trade,
                price: Some(parse_required::<Price>(Some(row.price))?),
                quantity: Some(parse_required::<Quantity>(Some(row.size))?),
                rate: None,
                ask_price: None,
                ask_quantity: None,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: None,
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: row.sequence_number.map(Sequence::new),
                observed_at_unix_nanos: row.sip_timestamp_unix_nanos.into(),
            })
        })
        .collect()
}

fn normalize_historical(
    rows: Vec<crate::services::participants::massive::connection::MassiveHistoricalBar>,
    request: &HistoricalMarketRequest,
    interval: &str,
    market_type: MarketType,
) -> Result<Vec<MarketEvent>, IntegrationError> {
    rows.into_iter()
        .map(|row| -> Result<MarketEvent, IntegrationError> {
            Ok(MarketEvent {
                symbol: Symbol::new(request.symbol.as_str())
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
                    timeframe: interval.into(),
                    open: row
                        .open
                        .parse::<Price>()
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    high: row
                        .high
                        .parse::<Price>()
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    low: row
                        .low
                        .parse::<Price>()
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    close: row
                        .close
                        .parse::<Price>()
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    volume: row
                        .volume
                        .map(|value| value.parse::<Quantity>())
                        .transpose()
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    derivation: match market_type {
                        MarketType::Equity => "massive-stocks-aggregate".into(),
                        MarketType::Option => "massive-options-aggregate".into(),
                    },
                }),
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: None,
                observed_at_unix_nanos: ((row.open_time_unix_millis as u64) * 1_000_000).into(),
            })
        })
        .collect()
}

fn parse_interval(value: &str) -> Result<(u32, &'static str), IntegrationError> {
    let value = value.trim().to_ascii_lowercase();
    let split = value
        .find(|character: char| !character.is_ascii_digit())
        .ok_or_else(|| IntegrationError::InvalidRequest("invalid historical interval".into()))?;
    let multiplier = value[..split]
        .parse::<u32>()
        .map_err(|_| IntegrationError::InvalidRequest("invalid historical interval".into()))?;
    let timespan = match &value[split..] {
        "s" | "sec" | "second" | "seconds" => "second",
        "m" | "min" | "minute" | "minutes" => "minute",
        "h" | "hour" | "hours" => "hour",
        "d" | "day" | "days" => "day",
        "w" | "week" | "weeks" => "week",
        other => {
            return Err(IntegrationError::InvalidRequest(format!(
                "unsupported Massive interval unit: {other}"
            )))
        }
    };
    if multiplier == 0 {
        return Err(IntegrationError::InvalidRequest(
            "historical interval must be positive".into(),
        ));
    }
    Ok((multiplier, timespan))
}

impl MassiveAsyncMarketStream {
    pub(crate) fn new(
        api_key: impl Into<String>,
        endpoint: impl Into<String>,
        market_type: MarketType,
        event_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        if event_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Massive event queue capacity must be positive".into(),
            ));
        }
        let endpoint = websocket_endpoint(endpoint.into())?;
        let name = match market_type {
            MarketType::Equity => "equity",
            MarketType::Option => "options",
        };
        let identity = ConnectionDescriptor::new(
            format!("market.massive.{name}.websocket.async"),
            crate::domain::ParticipantRef::new(
                crate::domain::ParticipantKind::DataProvider,
                "massive",
            )
            .expect("static Massive participant"),
            "market-data",
        )
        .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(identity),
            api_key: api_key.into(),
            endpoint,
            socket: None,
            subscriptions: BTreeMap::new(),
            next_subscription_id: 1,
            event_capacity,
        })
    }

    async fn send(&self, value: Value) -> Result<(), IntegrationError> {
        self.socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .send_text(value.to_string())
            .await
            .map_err(IntegrationError::Transport)
    }
}

impl AsyncMarketEventSource for MassiveAsyncMarketStream {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.state.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        if self.api_key.trim().is_empty() {
            return Err(IntegrationError::Authentication(
                "Massive market stream API key is required".into(),
            ));
        }
        self.state.lifecycle = ConnectionLifecycle::Starting;
        self.socket = Some(
            AsyncTokioSocket::connect(&self.endpoint, self.event_capacity)
                .await
                .map_err(IntegrationError::Transport)?,
        );
        self.send(json!({"action":"auth","params":self.api_key}))
            .await?;
        // A reconnect creates a new provider session. Keep the capability's
        // subscription projection across transport disconnects and restore it
        // only after the new socket has authenticated.
        for symbols in self.subscriptions.values() {
            let params = symbols
                .iter()
                .map(|symbol| format!("Q.{}", symbol.to_ascii_uppercase()))
                .collect::<Vec<_>>()
                .join(",");
            self.send(json!({"action":"subscribe","params":params}))
                .await?;
        }
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.authenticated = true;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos().into());
        Ok(())
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        self.state.authenticated = false;
        Ok(())
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }

    async fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        let params = request
            .symbols
            .iter()
            .map(|symbol| format!("Q.{}", symbol.to_ascii_uppercase()))
            .collect::<Vec<_>>()
            .join(",");
        self.send(json!({"action":"subscribe","params":params}))
            .await?;
        let id = SubscriptionId(self.next_subscription_id);
        self.next_subscription_id += 1;
        self.subscriptions.insert(id, request.symbols);
        Ok(id)
    }

    async fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        let symbols = self.subscriptions.remove(&subscription).ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown Massive market subscription".into())
        })?;
        let params = symbols
            .iter()
            .map(|symbol| format!("Q.{}", symbol.to_ascii_uppercase()))
            .collect::<Vec<_>>()
            .join(",");
        self.send(json!({"action":"unsubscribe","params":params}))
            .await
    }

    async fn next_market_event(&mut self) -> Result<MarketEvent, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        loop {
            let event = self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .next_event()
                .await;
            let message = match event {
                AsyncSocketEvent::Message(message) => message,
                AsyncSocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
                AsyncSocketEvent::Backpressure => {
                    return Err(IntegrationError::Backpressure(
                        "Massive market event queue overflowed".into(),
                    ))
                }
            };
            let text = match message {
                Message::Text(text) => text,
                Message::Ping(payload) => {
                    self.socket
                        .as_ref()
                        .expect("connected Massive socket")
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                    continue;
                }
                Message::Close(_) => {
                    return Err(IntegrationError::Transport(
                        "Massive market WebSocket closed".into(),
                    ))
                }
                _ => continue,
            };
            let values: Value = serde_json::from_str(text.as_ref())
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
            for row in values.as_array().cloned().unwrap_or_else(|| vec![values]) {
                if let Some(event) = normalize(&row)? {
                    return Ok(event);
                }
            }
        }
    }
}

fn websocket_endpoint(endpoint: String) -> Result<String, IntegrationError> {
    let endpoint = if let Some(rest) = endpoint.strip_prefix("http://") {
        format!("wss://{rest}")
    } else if let Some(rest) = endpoint.strip_prefix("https://") {
        format!("wss://{rest}")
    } else {
        endpoint
    };
    let endpoint = endpoint.trim_end_matches('/').to_string();
    if endpoint.is_empty() {
        Err(IntegrationError::InvalidRequest(
            "Massive market endpoint is required".into(),
        ))
    } else {
        Ok(endpoint)
    }
}

fn normalize(value: &Value) -> Result<Option<MarketEvent>, IntegrationError> {
    let event = value.get("ev").and_then(Value::as_str).unwrap_or_default();
    if event == "status" || event == "status_update" {
        return Ok(None);
    }
    let symbol = value
        .get("sym")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_ascii_uppercase();
    if symbol.is_empty() {
        return Ok(None);
    }
    let timestamp = value
        .get("t")
        .and_then(Value::as_u64)
        .unwrap_or_else(now_unix_nanos);
    let timestamp = if timestamp < 10_000_000_000_000 {
        timestamp.saturating_mul(1_000_000)
    } else {
        timestamp
    };
    match event {
        "Q" => Ok(Some(MarketEvent {
            symbol: Symbol::new(symbol)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            kind: MarketEventKind::Quote,
            price: parse_optional::<Price>(text(value, "bp").or_else(|| text(value, "ap")))?,
            quantity: parse_optional::<Quantity>(text(value, "bs"))?,
            rate: None,
            ask_price: parse_optional::<Price>(text(value, "ap"))?,
            ask_quantity: parse_optional::<Quantity>(text(value, "as"))?,
            bids: Vec::new(),
            asks: Vec::new(),
            bar: None,
            greeks: None,
            first_sequence: None,
            last_sequence: None,
            sequence: value.get("q").and_then(Value::as_u64).map(Sequence::new),
            observed_at_unix_nanos: timestamp.into(),
        })),
        "T" => Ok(Some(MarketEvent {
            symbol: Symbol::new(symbol)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            kind: MarketEventKind::Trade,
            price: Some(parse_required::<Price>(text(value, "p"))?),
            quantity: Some(parse_required::<Quantity>(text(value, "s"))?),
            rate: None,
            ask_price: None,
            ask_quantity: None,
            bids: Vec::new(),
            asks: Vec::new(),
            bar: None,
            greeks: None,
            first_sequence: None,
            last_sequence: None,
            sequence: value.get("q").and_then(Value::as_u64).map(Sequence::new),
            observed_at_unix_nanos: timestamp.into(),
        })),
        _ => Ok(None),
    }
}

fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
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

fn parse_required<T>(value: Option<String>) -> Result<T, IntegrationError>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    parse_optional(value)?
        .ok_or_else(|| IntegrationError::InvalidPayload("Massive market field is missing".into()))
}
fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::{MarketType, MassiveAsyncMarketStream};
    use crate::application::{AsyncMarketEventSource, SubscriptionId};

    #[tokio::test]
    async fn transport_disconnect_preserves_desired_subscriptions_for_reconnect() {
        let mut stream = MassiveAsyncMarketStream::new(
            "credential",
            "wss://example.invalid/stocks",
            MarketType::Equity,
            8,
        )
        .unwrap();
        stream
            .subscriptions
            .insert(SubscriptionId(7), vec!["AAPL".into()]);

        stream.disconnect_channel().await.unwrap();

        assert_eq!(
            stream.subscriptions.get(&SubscriptionId(7)).unwrap(),
            &["AAPL"]
        );
    }
}
