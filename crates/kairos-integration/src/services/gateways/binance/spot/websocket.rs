//! Binance Spot public market WebSocket.
//!
//! The provider-specific wire format is translated here into the integration
//! application's `MarketEvent` vocabulary.  Market owns no Binance symbols,
//! request ids, or JSON payloads beyond this boundary.

use std::collections::{BTreeMap, VecDeque};
use std::io::ErrorKind;
use std::net::TcpStream;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tungstenite::{connect, Message, WebSocket};

use crate::application::connection::Connection;
use crate::application::error::IntegrationError;
use crate::application::market_stream::{
    MarketStreamConnection, MarketSubscription, SubscriptionId,
};
use crate::domain::{
    AccessScope, ConnectionHealth, ConnectionIdentity, ConnectionLifecycle, ConnectionState,
    IntegrationCapability, MarketEvent, MarketEventKind, ProductFamily, TransportKind,
};

type Socket = WebSocket<tungstenite::stream::MaybeTlsStream<TcpStream>>;

pub struct BinanceSpotWebSocketMarketStream {
    identity: ConnectionIdentity,
    state: ConnectionState,
    endpoint: String,
    rest_endpoint: String,
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
        let identity = ConnectionIdentity::new(
            "market.binance.spot.websocket",
            crate::domain::IntegrationRoute::exchange("binance"),
            Some(ProductFamily::Spot),
            AccessScope::Public,
            TransportKind::WebSocket,
            IntegrationCapability::MarketStream,
        )
        .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(identity.clone()),
            identity,
            endpoint,
            rest_endpoint,
            socket: None,
            pending_events: VecDeque::new(),
            snapshot_sequences: BTreeMap::new(),
            subscriptions: BTreeMap::new(),
            next_subscription_id: 1,
        })
    }

    fn open(&mut self) -> Result<(), String> {
        let (socket, _) = connect(self.endpoint.as_str()).map_err(|error| error.to_string())?;
        self.socket = Some(socket);
        self.set_read_timeout(Duration::from_millis(100))?;
        let subscriptions: Vec<Vec<String>> = self.subscriptions.values().cloned().collect();
        for symbols in subscriptions {
            self.send_subscription("SUBSCRIBE", &symbols)?;
            self.queue_snapshots(&symbols)?;
        }
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos());
        self.state.last_error = None;
        Ok(())
    }

    fn set_read_timeout(&mut self, timeout: Duration) -> Result<(), String> {
        let stream = self
            .socket
            .as_mut()
            .ok_or_else(|| "Binance Spot WebSocket is not connected".to_string())?
            .get_mut();
        match stream {
            tungstenite::stream::MaybeTlsStream::Plain(stream) => stream
                .set_read_timeout(Some(timeout))
                .map_err(|error| error.to_string()),
            tungstenite::stream::MaybeTlsStream::NativeTls(stream) => stream
                .get_ref()
                .set_read_timeout(Some(timeout))
                .map_err(|error| error.to_string()),
            _ => Err("unsupported Binance WebSocket transport".into()),
        }
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
            .as_mut()
            .ok_or_else(|| "Binance Spot WebSocket is not connected".to_string())?
            .send(Message::Text(message.to_string().into()))
            .map_err(|error| error.to_string())
    }

    fn queue_snapshots(&mut self, symbols: &[String]) -> Result<(), String> {
        let client = reqwest::blocking::Client::new();
        for symbol in symbols {
            let payload: Value = client
                .get(format!("{}/api/v3/depth", self.rest_endpoint))
                .query(&[("symbol", symbol.as_str()), ("limit", "1000")])
                .send()
                .map_err(|error| error.to_string())?
                .error_for_status()
                .map_err(|error| error.to_string())?
                .json()
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
        match kind {
            "trade" => Ok(Some(MarketEvent {
                symbol,
                kind: MarketEventKind::Trade,
                price: string_field(&value, "p"),
                quantity: string_field(&value, "q"),
                ask_price: None,
                ask_quantity: None,
                bids: Vec::new(),
                asks: Vec::new(),
                first_sequence: None,
                last_sequence: None,
                sequence: value.get("t").and_then(Value::as_u64),
                observed_at_unix_nanos,
            })),
            "bookTicker" => Ok(Some(MarketEvent {
                symbol,
                kind: MarketEventKind::Quote,
                price: string_field(&value, "b"),
                quantity: string_field(&value, "B"),
                ask_price: string_field(&value, "a"),
                ask_quantity: string_field(&value, "A"),
                bids: Vec::new(),
                asks: Vec::new(),
                first_sequence: None,
                last_sequence: None,
                sequence: value.get("u").and_then(Value::as_u64),
                observed_at_unix_nanos,
            })),
            "depthUpdate" => Ok(Some(MarketEvent {
                symbol,
                kind: MarketEventKind::BookDelta,
                price: None,
                quantity: None,
                ask_price: None,
                ask_quantity: None,
                bids: levels(&value, "b")?,
                asks: levels(&value, "a")?,
                first_sequence: value.get("U").and_then(Value::as_u64),
                last_sequence: value.get("u").and_then(Value::as_u64),
                sequence: value.get("u").and_then(Value::as_u64),
                observed_at_unix_nanos,
            })),
            _ => Ok(None),
        }
    }

    fn align_depth_event(&mut self, mut event: MarketEvent) -> Result<Option<MarketEvent>, String> {
        if event.kind != MarketEventKind::BookDelta {
            return Ok(Some(event));
        }
        let symbol = event.symbol.clone();
        let Some(snapshot_sequence) = self.snapshot_sequences.get(&symbol).copied() else {
            return Ok(Some(event));
        };
        let last = event
            .last_sequence
            .ok_or_else(|| "Binance depth event has no final sequence".to_string())?;
        let first = event
            .first_sequence
            .ok_or_else(|| "Binance depth event has no first sequence".to_string())?;
        if last <= snapshot_sequence {
            return Ok(None);
        }
        let expected = snapshot_sequence.saturating_add(1);
        if first > expected {
            return Err(format!(
                "Binance depth gap after snapshot: expected {expected}, got {first}"
            ));
        }
        event.first_sequence = Some(expected);
        self.snapshot_sequences.remove(&symbol);
        Ok(Some(event))
    }
}

impl Connection for BinanceSpotWebSocketMarketStream {
    fn identity(&self) -> &ConnectionIdentity {
        &self.identity
    }

    fn state(&self) -> &ConnectionState {
        &self.state
    }

    fn start(&mut self) -> Result<(), String> {
        if self.state.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.state.lifecycle = ConnectionLifecycle::Starting;
        if let Err(error) = self.open() {
            self.state.lifecycle = ConnectionLifecycle::Degraded;
            self.state.last_error = Some(error.clone());
            return Err(error);
        }
        Ok(())
    }

    fn stop(&mut self) -> Result<(), String> {
        if let Some(mut socket) = self.socket.take() {
            let _ = socket.close(None);
        }
        self.pending_events.clear();
        self.snapshot_sequences.clear();
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect(&mut self) -> Result<(), String> {
        let _ = self.stop();
        self.state.reconnect_count = self.state.reconnect_count.saturating_add(1);
        self.start()
    }

    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }
}

impl MarketStreamConnection for BinanceSpotWebSocketMarketStream {
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
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .read()
            {
                Ok(message) => message,
                Err(tungstenite::Error::Io(error))
                    if matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) =>
                {
                    return Ok(None);
                }
                Err(error) => {
                    self.state.lifecycle = ConnectionLifecycle::Degraded;
                    self.state.last_error = Some(error.to_string());
                    return Err(IntegrationError::Transport(error.to_string()));
                }
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
                            .send(Message::Pong(payload))
                            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
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

fn snapshot_event(symbol: &str, value: &Value, sequence: u64) -> Result<MarketEvent, String> {
    Ok(MarketEvent {
        symbol: symbol.to_string(),
        kind: MarketEventKind::BookSnapshot,
        price: None,
        quantity: None,
        ask_price: None,
        ask_quantity: None,
        bids: levels(value, "bids")?,
        asks: levels(value, "asks")?,
        first_sequence: Some(sequence),
        last_sequence: Some(sequence),
        sequence: Some(sequence),
        observed_at_unix_nanos: now_unix_nanos(),
    })
}

fn levels(value: &Value, key: &str) -> Result<Vec<(String, String)>, String> {
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
            Ok((price.to_string(), quantity.to_string()))
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
        assert_eq!(trade.sequence, Some(42));

        let quote = BinanceSpotWebSocketMarketStream::parse_event(
            r#"{"u":9,"s":"BTCUSDT","b":"100","B":"2","a":"101","A":"3"}"#,
        )
        .unwrap()
        .unwrap();
        assert_eq!(quote.kind, MarketEventKind::Quote);
        assert_eq!(quote.ask_price.as_deref(), Some("101"));
        assert_eq!(quote.ask_quantity.as_deref(), Some("3"));

        let depth = BinanceSpotWebSocketMarketStream::parse_event(
            r#"{"e":"depthUpdate","E":1700000000000,"s":"BTCUSDT","U":10,"u":11,"b":[["100","2"]],"a":[["101","3"]]}"#,
        )
        .unwrap()
        .unwrap();
        assert_eq!(depth.kind, MarketEventKind::BookDelta);
        assert_eq!(depth.first_sequence, Some(10));
        assert_eq!(depth.last_sequence, Some(11));
        assert_eq!(depth.bids, vec![("100".into(), "2".into())]);
    }

    #[test]
    fn validates_websocket_endpoint_before_network_access() {
        assert!(BinanceSpotWebSocketMarketStream::new("https://example.test").is_err());
    }
}
