//! Binance Spot public market WebSocket.
//!
//! The provider-specific wire format is translated here into the integration
//! application's `MarketEvent` vocabulary.  Market owns no Binance symbols,
//! request ids, or JSON payloads beyond this boundary.

use std::collections::{BTreeMap, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use kairos_domain_types::{Price, Quantity, Sequence, Symbol};
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

use crate::application::capabilities::market::{
    MarketStreamConnection, MarketSubscription, SubscriptionId,
};
use crate::application::capabilities::{
    ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState, MarketBar,
    MarketEvent, MarketEventKind,
};
use crate::application::error::IntegrationError;
use crate::services::transport::http::PublicHttpClient;
use crate::services::transport::websocket::{SocketEvent, TokioSocket};

type Socket = TokioSocket;

pub struct BinanceSpotWebSocketMarketStream {
    identity: ConnectionDescriptor,
    state: ConnectionState,
    endpoint: String,
    rest_endpoint: String,
    http: PublicHttpClient,
    socket: Option<Socket>,
    pending_events: VecDeque<MarketEvent>,
    snapshot_sequences: BTreeMap<String, u64>,
    subscriptions: BTreeMap<SubscriptionId, Vec<String>>,
    next_subscription_id: u64,
}

impl BinanceSpotWebSocketMarketStream {
    pub fn new(endpoint: impl Into<String>) -> Result<Self, IntegrationError> {
        Self::with_rest_endpoint(endpoint, "https://api.binance.com")
    }

    pub fn with_rest_endpoint(
        endpoint: impl Into<String>,
        rest_endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into().trim_end_matches('/').to_string();
        let rest_endpoint = rest_endpoint.into().trim_end_matches('/').to_string();
        if !(endpoint.starts_with("wss://") || endpoint.starts_with("ws://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        let identity = ConnectionDescriptor::new(
            "market.binance.spot.websocket",
            crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Exchange, "binance")
                .expect("static Binance participant"),
            "spot",
        )
        .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(identity.clone()),
            identity,
            endpoint,
            rest_endpoint,
            http: PublicHttpClient::new("kairos-integration/binance-spot-websocket")
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
            socket: None,
            pending_events: VecDeque::new(),
            snapshot_sequences: BTreeMap::new(),
            subscriptions: BTreeMap::new(),
            next_subscription_id: 1,
        })
    }

    fn open(&mut self) -> Result<(), String> {
        self.socket = Some(TokioSocket::connect(self.endpoint.clone())?);
        let subscriptions: Vec<Vec<String>> = self.subscriptions.values().cloned().collect();
        for symbols in subscriptions {
            self.send_subscription("SUBSCRIBE", &symbols)?;
            self.queue_snapshots(&symbols)?;
        }
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos().into());
        self.state.last_error = None;
        Ok(())
    }

    fn send_subscription(&mut self, method: &str, symbols: &[String]) -> Result<(), String> {
        let params = symbols
            .iter()
            .flat_map(|symbol| {
                let symbol = symbol.to_ascii_lowercase();
                [
                    format!("{symbol}@trade"),
                    format!("{symbol}@bookTicker"),
                    format!("{symbol}@depth@100ms"),
                    format!("{symbol}@kline_1m"),
                ]
            })
            .collect::<Vec<_>>();
        let message = json!({
            "method": method,
            "params": params,
            "id": self.next_subscription_id,
        });
        self.next_subscription_id = self.next_subscription_id.saturating_add(1);
        self.socket
            .as_ref()
            .ok_or_else(|| "Binance Spot WebSocket is not connected".to_string())?
            .send_text(message.to_string())
    }

    fn queue_snapshots(&mut self, symbols: &[String]) -> Result<(), String> {
        for symbol in symbols {
            let payload: Value = self
                .http
                .get_json_with_query(
                    &format!("{}/api/v3/depth", self.rest_endpoint),
                    &[("symbol", symbol.clone()), ("limit", "1000".into())],
                )
                .map_err(|error| error.to_string())?;
            let sequence = payload
                .get("lastUpdateId")
                .and_then(Value::as_u64)
                .ok_or_else(|| "Binance depth snapshot has no lastUpdateId".to_string())?;
            let symbol = symbol.to_ascii_uppercase();
            self.snapshot_sequences.insert(symbol.clone(), sequence);
            self.pending_events
                .push_back(snapshot_event(&symbol, &payload, sequence)?);
        }
        Ok(())
    }

    fn parse_event(payload: &str) -> Result<Option<MarketEvent>, String> {
        let value: Value = serde_json::from_str(payload).map_err(|error| error.to_string())?;
        if value.get("result").is_some() || value.get("id").is_some() && value.get("e").is_none() {
            return Ok(None);
        }
        let kind = value
            .get("e")
            .and_then(Value::as_str)
            .or_else(|| {
                (value.get("b").is_some()
                    && value.get("B").is_some()
                    && value.get("a").is_some()
                    && value.get("A").is_some())
                .then_some("bookTicker")
            })
            .unwrap_or_default();
        let observed_at_unix_nanos = value
            .get("E")
            .and_then(Value::as_u64)
            .map(|milliseconds| milliseconds.saturating_mul(1_000_000))
            .unwrap_or_else(now_unix_nanos);
        let symbol = value
            .get("s")
            .and_then(Value::as_str)
            .ok_or_else(|| "Binance market event has no symbol".to_string())?
            .to_ascii_uppercase();
        let symbol = Symbol::new(symbol)?;
        match kind {
            "trade" => Ok(Some(MarketEvent {
                symbol,
                kind: MarketEventKind::Trade,
                price: Some(parse_required::<Price>(&value, "p")?),
                quantity: Some(parse_required::<Quantity>(&value, "q")?),
                rate: None,
                ask_price: None,
                ask_quantity: None,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: None,
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: value.get("t").and_then(Value::as_u64).map(Sequence::new),
                observed_at_unix_nanos: observed_at_unix_nanos.into(),
            })),
            "bookTicker" => Ok(Some(MarketEvent {
                symbol,
                kind: MarketEventKind::Quote,
                price: parse_optional(&value, "b")?.or(parse_optional(&value, "a")?),
                quantity: parse_optional(&value, "B")?,
                rate: None,
                ask_price: parse_optional(&value, "a")?,
                ask_quantity: parse_optional(&value, "A")?,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: None,
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: value.get("u").and_then(Value::as_u64).map(Sequence::new),
                observed_at_unix_nanos: observed_at_unix_nanos.into(),
            })),
            "depthUpdate" => Ok(Some(MarketEvent {
                symbol,
                kind: MarketEventKind::BookDelta,
                price: None,
                quantity: None,
                rate: None,
                ask_price: None,
                ask_quantity: None,
                bids: levels(&value, "b")?,
                asks: levels(&value, "a")?,
                bar: None,
                greeks: None,
                first_sequence: value.get("U").and_then(Value::as_u64).map(Sequence::new),
                last_sequence: value.get("u").and_then(Value::as_u64).map(Sequence::new),
                sequence: value.get("u").and_then(Value::as_u64).map(Sequence::new),
                observed_at_unix_nanos: observed_at_unix_nanos.into(),
            })),
            "kline" => {
                let kline = value
                    .get("k")
                    .ok_or_else(|| "Binance kline event has no kline payload".to_string())?;
                Ok(Some(MarketEvent {
                    symbol,
                    kind: MarketEventKind::Bar,
                    price: None,
                    quantity: None,
                    rate: None,
                    ask_price: None,
                    ask_quantity: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                    bar: Some(MarketBar {
                        timeframe: string_field(kline, "i").unwrap_or_else(|| "1m".into()),
                        open: required_string_field(kline, "o")?
                            .parse::<Price>()
                            .map_err(|error| error.to_string())?,
                        high: required_string_field(kline, "h")?
                            .parse::<Price>()
                            .map_err(|error| error.to_string())?,
                        low: required_string_field(kline, "l")?
                            .parse::<Price>()
                            .map_err(|error| error.to_string())?,
                        close: required_string_field(kline, "c")?
                            .parse::<Price>()
                            .map_err(|error| error.to_string())?,
                        volume: string_field(kline, "v")
                            .map(|value| value.parse::<Quantity>())
                            .transpose()
                            .map_err(|error| error.to_string())?,
                        derivation: "binance-kline".into(),
                    }),
                    greeks: None,
                    first_sequence: None,
                    last_sequence: None,
                    sequence: kline.get("L").and_then(Value::as_u64).map(Sequence::new),
                    observed_at_unix_nanos: observed_at_unix_nanos.into(),
                }))
            }
            _ => Ok(None),
        }
    }

    fn align_depth_event(&mut self, mut event: MarketEvent) -> Result<Option<MarketEvent>, String> {
        if event.kind != MarketEventKind::BookDelta {
            return Ok(Some(event));
        }
        let symbol = event.symbol.clone();
        let Some(snapshot_sequence) = self.snapshot_sequences.get(symbol.as_str()).copied() else {
            return Ok(Some(event));
        };
        let last = event
            .last_sequence
            .ok_or_else(|| "Binance depth event has no final sequence".to_string())?;
        let first = event
            .first_sequence
            .ok_or_else(|| "Binance depth event has no first sequence".to_string())?;
        if last.get() <= snapshot_sequence {
            return Ok(None);
        }
        let expected = snapshot_sequence.saturating_add(1);
        if first.get() > expected {
            return Err(format!(
                "Binance depth gap after snapshot: expected {expected}, got {first}"
            ));
        }
        event.first_sequence = Some(Sequence::new(expected));
        self.snapshot_sequences.remove(symbol.as_str());
        Ok(Some(event))
    }
}

impl MarketStreamConnection for BinanceSpotWebSocketMarketStream {
    fn descriptor(&self) -> &ConnectionDescriptor {
        &self.identity
    }

    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.state.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.state.lifecycle = ConnectionLifecycle::Starting;
        self.open().map_err(|error| {
            self.state.lifecycle = ConnectionLifecycle::Failed;
            self.state.last_error = Some(error.clone());
            IntegrationError::Transport(error)
        })
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.socket.take();
        self.pending_events.clear();
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.socket.take();
        self.state.reconnect_count = self.state.reconnect_count.saturating_add(1);
        self.state.lifecycle = ConnectionLifecycle::Starting;
        self.open().map_err(|error| {
            self.state.lifecycle = ConnectionLifecycle::Failed;
            self.state.last_error = Some(error.clone());
            IntegrationError::Transport(error)
        })
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: false,
            last_error: self.state.last_error.clone(),
        }
    }

    fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        self.send_subscription("SUBSCRIBE", &request.symbols)
            .map_err(IntegrationError::Transport)?;
        self.queue_snapshots(&request.symbols)
            .map_err(IntegrationError::Transport)?;
        let id = SubscriptionId(self.next_subscription_id);
        self.next_subscription_id = self.next_subscription_id.saturating_add(1);
        self.subscriptions.insert(id, request.symbols);
        Ok(id)
    }

    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        let symbols = self.subscriptions.remove(&subscription).ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown market subscription".into())
        })?;
        for symbol in &symbols {
            let symbol = symbol.to_ascii_uppercase();
            self.snapshot_sequences.remove(&symbol);
            self.pending_events.retain(|event| event.symbol != symbol);
        }
        self.send_subscription("UNSUBSCRIBE", &symbols)
            .map_err(IntegrationError::Transport)
    }

    fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        loop {
            if let Some(event) = self.pending_events.pop_front() {
                return Ok(Some(event));
            }
            let message = match self
                .socket
                .as_ref()
                .ok_or(IntegrationError::NotReady)?
                .try_recv()
                .map_err(IntegrationError::Transport)?
            {
                Some(SocketEvent::Message(message)) => message,
                Some(SocketEvent::Error(error)) => {
                    self.state.lifecycle = ConnectionLifecycle::Degraded;
                    self.state.last_error = Some(error.clone());
                    return Err(IntegrationError::Transport(error));
                }
                Some(SocketEvent::Backpressure) => {
                    self.state.lifecycle = ConnectionLifecycle::Degraded;
                    self.state.last_error = Some("market event queue overflowed".into());
                    return Err(IntegrationError::Backpressure(
                        "Binance Spot market event queue overflowed".into(),
                    ));
                }
                None => return Ok(None),
            };
            match message {
                Message::Text(text) => {
                    if let Some(event) = Self::parse_event(text.as_ref())
                        .map_err(IntegrationError::InvalidPayload)?
                    {
                        if let Some(event) = self
                            .align_depth_event(event)
                            .map_err(IntegrationError::InvalidPayload)?
                        {
                            return Ok(Some(event));
                        }
                    }
                }
                Message::Ping(payload) => {
                    if let Some(socket) = self.socket.as_mut() {
                        socket
                            .send_pong(payload.to_vec())
                            .map_err(IntegrationError::Transport)?;
                    }
                }
                Message::Close(_) => {
                    self.state.lifecycle = ConnectionLifecycle::Degraded;
                    return Err(IntegrationError::Transport(
                        "Binance WebSocket closed".into(),
                    ));
                }
                Message::Binary(_) | Message::Pong(_) | Message::Frame(_) => {}
            }
        }
    }
}

fn string_field(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_string)
}

fn required_string_field(value: &Value, key: &str) -> Result<String, String> {
    string_field(value, key).ok_or_else(|| format!("Binance kline event has no {key} field"))
}

fn parse_optional<T>(value: &Value, key: &str) -> Result<Option<T>, String>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    string_field(value, key)
        .map(|value| value.parse::<T>().map_err(|error| error.to_string()))
        .transpose()
}

fn parse_required<T>(value: &Value, key: &str) -> Result<T, String>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    parse_optional(value, key)?.ok_or_else(|| format!("Binance market event has no {key}"))
}

fn snapshot_event(symbol: &str, value: &Value, sequence: u64) -> Result<MarketEvent, String> {
    Ok(MarketEvent {
        symbol: Symbol::new(symbol.to_string())?,
        kind: MarketEventKind::BookSnapshot,
        price: None,
        quantity: None,
        rate: None,
        ask_price: None,
        ask_quantity: None,
        bids: levels(value, "bids")?,
        asks: levels(value, "asks")?,
        bar: None,
        greeks: None,
        first_sequence: Some(Sequence::new(sequence)),
        last_sequence: Some(Sequence::new(sequence)),
        sequence: Some(Sequence::new(sequence)),
        observed_at_unix_nanos: now_unix_nanos().into(),
    })
}

fn levels(value: &Value, key: &str) -> Result<Vec<(Price, Quantity)>, String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("Binance depth event has no {key} levels"))?
        .iter()
        .map(|level| {
            let values = level
                .as_array()
                .ok_or_else(|| "Binance depth level is not an array".to_string())?;
            let price = values
                .first()
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance depth level has no price".to_string())?;
            let quantity = values
                .get(1)
                .and_then(Value::as_str)
                .ok_or_else(|| "Binance depth level has no quantity".to_string())?;
            Ok((price.parse::<Price>()?, quantity.parse::<Quantity>()?))
        })
        .collect()
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_trade_and_book_ticker_without_provider_payloads_escaping() {
        let trade = BinanceSpotWebSocketMarketStream::parse_event(
            r#"{"e":"trade","E":1700000000000,"s":"btcusdt","t":42,"p":"100.1","q":"0.2"}"#,
        )
        .unwrap()
        .unwrap();
        assert_eq!(trade.kind, MarketEventKind::Trade);
        assert_eq!(trade.symbol, "BTCUSDT");
        assert_eq!(trade.sequence.map(|value| value.get()), Some(42));

        let quote = BinanceSpotWebSocketMarketStream::parse_event(
            r#"{"u":9,"s":"BTCUSDT","b":"100","B":"2","a":"101","A":"3"}"#,
        )
        .unwrap()
        .unwrap();
        assert_eq!(quote.kind, MarketEventKind::Quote);
        assert_eq!(
            quote.ask_price.map(|value| value.to_string()),
            Some("101".into())
        );
        assert_eq!(
            quote.ask_quantity.map(|value| value.to_string()),
            Some("3".into())
        );

        let depth = BinanceSpotWebSocketMarketStream::parse_event(
            r#"{"e":"depthUpdate","E":1700000000000,"s":"BTCUSDT","U":10,"u":11,"b":[["100","2"]],"a":[["101","3"]]}"#,
        )
        .unwrap()
        .unwrap();
        assert_eq!(depth.kind, MarketEventKind::BookDelta);
        assert_eq!(depth.first_sequence.map(|value| value.get()), Some(10));
        assert_eq!(depth.last_sequence.map(|value| value.get()), Some(11));
        assert_eq!(depth.bids[0].0.to_string(), "100");
        assert_eq!(depth.bids[0].1.to_string(), "2");

        let bar = BinanceSpotWebSocketMarketStream::parse_event(
            r#"{"e":"kline","E":1700000000000,"s":"BTCUSDT","k":{"i":"1m","o":"100","h":"102","l":"99","c":"101","v":"12.5","L":77}}"#,
        )
        .unwrap()
        .unwrap();
        assert_eq!(bar.kind, MarketEventKind::Bar);
        let bar_data = bar.bar.expect("bar payload");
        assert_eq!(bar_data.timeframe, "1m");
        assert_eq!(bar_data.close.to_string(), "101");
        assert_eq!(
            bar_data.volume.map(|value| value.to_string()),
            Some("12.5".into())
        );
    }

    #[test]
    fn validates_websocket_endpoint_before_network_access() {
        assert!(BinanceSpotWebSocketMarketStream::new("https://example.test").is_err());
    }

    #[test]
    fn provider_reconnect_reports_a_closed_local_socket() {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .unwrap();
            runtime.block_on(async move {
                let listener = tokio::net::TcpListener::from_std(listener).unwrap();
                let (stream, _) = listener.accept().await.unwrap();
                let socket = tokio_tungstenite::accept_async(stream).await.unwrap();
                drop(socket);
            });
        });

        let mut connection =
            BinanceSpotWebSocketMarketStream::new(format!("ws://{address}")).unwrap();
        connection.connect_channel().unwrap();
        server.join().unwrap();
        assert!(connection.reconnect_channel().is_err());
        assert_eq!(connection.state.reconnect_count, 1);
    }

    #[test]
    fn rejects_a_depth_sequence_gap_after_snapshot() {
        let mut connection = BinanceSpotWebSocketMarketStream::new("ws://127.0.0.1:1").unwrap();
        connection.snapshot_sequences.insert("BTCUSDT".into(), 10);
        let event = BinanceSpotWebSocketMarketStream::parse_event(
            r#"{"e":"depthUpdate","s":"BTCUSDT","U":13,"u":14,"b":[],"a":[]}"#,
        )
        .unwrap()
        .unwrap();
        let error = connection.align_depth_event(event).unwrap_err();
        assert!(error.contains("expected 11, got 13"));
    }
}
