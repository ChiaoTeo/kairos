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
    HistoricalMarketRequest, IntegrationError, MarketEvent, MarketEventKind,
    MarketStreamConnection, MarketSubscription, SubscriptionId,
};
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};
use crate::services::transport::websocket::{SocketEvent, TokioSocket};

use crate::services::participants::massive::{MassiveAsyncRestClient, MassiveStocksRestClient};

/// Massive-native market family. This private service type deliberately does
/// not reuse the legacy cross-participant product taxonomy.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum MarketType {
    Equity,
    Option,
}

pub(crate) struct MassiveMarketStream {
    identity: ConnectionDescriptor,
    state: ConnectionState,
    api_key: String,
    endpoint: String,
    socket: Option<TokioSocket>,
    subscriptions: BTreeMap<SubscriptionId, Vec<String>>,
    next_subscription_id: u64,
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
            )
            .await
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        normalize_historical(rows, request, interval, self.market_type)
    }
}

fn historical_capabilities() -> MarketStreamCapabilities {
    MarketStreamCapabilities {
        historical: [MarketDataKind::Bar, MarketDataKind::TradeBar]
            .into_iter()
            .collect(),
        ..Default::default()
    }
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
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.authenticated = true;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos().into());
        Ok(())
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.subscriptions.clear();
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

impl MassiveMarketStream {
    pub(crate) fn new(
        api_key: impl Into<String>,
        endpoint: impl Into<String>,
        market_type: MarketType,
    ) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into();
        // The private HTTP endpoint redirects to HTTPS. Use secure WebSocket
        // directly so tungstenite does not receive an unsupported `https://`
        // redirect location from the proxy.
        let endpoint = websocket_endpoint(endpoint)?;
        let name = match market_type {
            MarketType::Equity => "equity",
            MarketType::Option => "options",
        };
        let identity = ConnectionDescriptor::new(
            format!("market.massive.{name}.websocket"),
            crate::domain::ParticipantRef::new(
                crate::domain::ParticipantKind::DataProvider,
                "massive",
            )
            .expect("static Massive participant"),
            "market-data",
        )
        .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(identity.clone()),
            identity,
            api_key: api_key.into(),
            endpoint,
            socket: None,
            subscriptions: BTreeMap::new(),
            next_subscription_id: 1,
        })
    }

    fn send(&mut self, value: Value) -> Result<(), String> {
        self.socket
            .as_mut()
            .ok_or_else(|| "Massive market socket is not connected".to_string())?
            .send_text(value.to_string())
    }
}

impl MarketStreamConnection for MassiveMarketStream {
    fn descriptor(&self) -> &ConnectionDescriptor {
        &self.identity
    }

    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.state.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        if self.api_key.trim().is_empty() {
            return Err(IntegrationError::Authentication(
                "Massive market stream API key is required".into(),
            ));
        }
        self.state.lifecycle = ConnectionLifecycle::Starting;
        self.socket =
            Some(TokioSocket::connect(self.endpoint.clone()).map_err(IntegrationError::Transport)?);
        self.send(json!({"action":"auth","params":self.api_key}))
            .map_err(IntegrationError::Transport)?;
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.authenticated = true;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos().into());
        self.state.last_error = None;
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.socket.take();
        self.subscriptions.clear();
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        self.state.authenticated = false;
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.socket.take();
        self.state.reconnect_count = self.state.reconnect_count.saturating_add(1);
        self.connect_channel()
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }

    fn capabilities(&self) -> MarketStreamCapabilities {
        MarketStreamCapabilities {
            realtime: [MarketDataKind::Quote, MarketDataKind::Trade]
                .into_iter()
                .collect(),
            ..Default::default()
        }
    }

    fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        let channel = "Q";
        let params = request
            .symbols
            .iter()
            .map(|symbol| format!("{channel}.{}", symbol.to_ascii_uppercase()))
            .collect::<Vec<_>>()
            .join(",");
        self.send(json!({"action":"subscribe","params":params}))
            .map_err(IntegrationError::Transport)?;
        let id = SubscriptionId(self.next_subscription_id);
        self.next_subscription_id += 1;
        self.subscriptions.insert(id, request.symbols);
        Ok(id)
    }
    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        let symbols = self.subscriptions.remove(&subscription).ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown Massive market subscription".into())
        })?;
        let params = symbols
            .iter()
            .map(|symbol| format!("Q.{}", symbol.to_ascii_uppercase()))
            .collect::<Vec<_>>()
            .join(",");
        self.send(json!({"action":"unsubscribe","params":params}))
            .map_err(IntegrationError::Transport)
    }
    fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        loop {
            let event = self
                .socket
                .as_ref()
                .ok_or(IntegrationError::NotReady)?
                .try_recv()
                .map_err(IntegrationError::Transport)?;
            let Some(event) = event else { return Ok(None) };
            let message = match event {
                SocketEvent::Message(message) => message,
                SocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
                SocketEvent::Backpressure => {
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
                        .unwrap()
                        .send_pong(payload.to_vec())
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
            let rows = values.as_array().cloned().unwrap_or_else(|| vec![values]);
            for row in rows {
                if let Some(event) = normalize(&row)? {
                    return Ok(Some(event));
                }
            }
        }
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
    use super::{MarketType, MassiveMarketStream};
    use crate::application::{MarketEventKind, MarketStreamConnection, MarketSubscription};
    use futures_util::{SinkExt, StreamExt};
    use tokio::net::TcpListener;
    use tokio_tungstenite::{accept_async, tungstenite::Message};

    #[test]
    fn massive_market_stream_handles_provider_ping_and_sequence() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .unwrap();
            runtime.block_on(async move {
                let listener = TcpListener::from_std(listener).unwrap();
                let (stream, _) = listener.accept().await.unwrap();
                let mut socket = accept_async(stream).await.unwrap();
                let _auth = socket.next().await.unwrap().unwrap();
                socket.send(Message::Ping(vec![7, 8].into())).await.unwrap();
                loop {
                    match socket.next().await.unwrap().unwrap() {
                        Message::Pong(payload) => {
                            assert_eq!(payload.as_ref(), &[7, 8]);
                            break;
                        }
                        Message::Text(_) => continue,
                        other => panic!("unexpected provider message: {other:?}"),
                    }
                }
                socket
                    .send(Message::Text(
                        r#"{"ev":"Q","sym":"AAPL","bp":"100.0","bs":"2","ap":"101.0","as":"3","q":42,"t":1700000000000}"#.into(),
                    ))
                    .await
                    .unwrap();
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            });
        });

        let mut stream =
            MassiveMarketStream::new("test-key", format!("ws://{address}"), MarketType::Equity)
                .unwrap();
        stream.connect_channel().unwrap();
        stream
            .subscribe(MarketSubscription {
                symbols: vec!["AAPL".into()],
            })
            .unwrap();
        let event = (0..100)
            .find_map(|_| {
                let event = stream.next_event().unwrap();
                if event.is_none() {
                    std::thread::sleep(std::time::Duration::from_millis(1));
                }
                event
            })
            .expect("provider quote should arrive");
        assert_eq!(event.kind, MarketEventKind::Quote);
        assert_eq!(event.sequence.map(|value| value.get()), Some(42));
        server.join().unwrap();
    }
}
