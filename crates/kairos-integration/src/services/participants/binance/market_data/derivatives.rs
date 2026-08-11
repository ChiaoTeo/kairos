//! Binance Futures and Options public quote polling market streams.

use crate::application::capabilities::market_facts::MarketGreeks;
use crate::application::{IntegrationError, MarketEvent, MarketEventKind};
use crate::services::participants::binance::ConnectionDomain;
use crate::services::transport::http::PublicHttpClient;
use crate::services::transport::{RestPollingMarketStream, RestSnapshotReader};
use kairos_domain_types::{Price, Quantity, Rate, Sequence, Symbol};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet, HashSet, VecDeque};
use tokio_tungstenite::tungstenite::Message;

use crate::application::capabilities::market::{
    MarketStreamConnection, MarketSubscription, SubscriptionId,
};
use crate::application::capabilities::{
    ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState, MarketDataKind,
    MarketStreamCapabilities,
};
use crate::services::transport::websocket::{SocketEvent, TokioSocket};

pub struct BinanceDerivativesSnapshotReader {
    http: PublicHttpClient,
    endpoint: String,
    path: String,
    product: ConnectionDomain,
}

impl BinanceDerivativesSnapshotReader {
    pub fn new(
        endpoint: impl Into<String>,
        path: impl Into<String>,
        product: ConnectionDomain,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ConnectionDomain::UsdMFutures
                | ConnectionDomain::CoinMFutures
                | ConnectionDomain::Options
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance derivatives market requires futures or options product".into(),
            ));
        }
        let endpoint = endpoint.into().trim_end_matches('/').to_string();
        let path = path.into();
        if endpoint.is_empty() || path.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance derivatives market endpoint is required".into(),
            ));
        }
        Ok(Self {
            http: PublicHttpClient::new("kairos-integration/binance-derivatives-market")
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
            endpoint,
            path,
            product,
        })
    }
}

impl RestSnapshotReader for BinanceDerivativesSnapshotReader {
    fn capabilities(&self) -> MarketStreamCapabilities {
        let mut realtime: BTreeSet<MarketDataKind> = [MarketDataKind::Quote].into_iter().collect();
        if self.product == ConnectionDomain::Options {
            realtime.insert(MarketDataKind::Greeks);
        }
        MarketStreamCapabilities {
            realtime,
            ..Default::default()
        }
    }

    fn snapshot(&mut self, symbols: &[String]) -> Result<Vec<MarketEvent>, IntegrationError> {
        if self.product == ConnectionDomain::Options {
            return self.options_snapshot(symbols);
        }
        symbols
            .iter()
            .map(|symbol| {
                let payload = self
                    .http
                    .get_json_with_query(
                        &format!("{}{}", self.endpoint, self.path),
                        &[("symbol", symbol.clone())],
                    )
                    .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                let value = if self.product == ConnectionDomain::Options {
                    payload
                        .as_array()
                        .and_then(|values| values.first())
                        .cloned()
                        .unwrap_or(payload)
                } else {
                    payload
                };
                let quote = quote_event(&value, symbol)?;
                let mut events = vec![quote];
                if self.product == ConnectionDomain::Options {
                    let mark = self
                        .http
                        .get_json_with_query(
                            &format!("{}/eapi/v1/mark", self.endpoint),
                            &[("symbol", symbol.clone())],
                        )
                        .map_err(|error| IntegrationError::Transport(error.to_string()))?;
                    let mark = mark
                        .as_array()
                        .and_then(|values| values.first())
                        .cloned()
                        .unwrap_or(mark);
                    if let Some(greeks) = greeks_event(&mark, symbol) {
                        events.push(greeks);
                    }
                }
                Ok(events)
            })
            .collect::<Result<Vec<_>, _>>()
            .map(|events| events.into_iter().flatten().collect())
    }
}

impl BinanceDerivativesSnapshotReader {
    fn options_snapshot(
        &mut self,
        symbols: &[String],
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        let requested = symbols
            .iter()
            .map(|symbol| symbol.to_ascii_uppercase())
            .collect::<HashSet<_>>();
        let tickers = self
            .http
            .get_json_with_query(&format!("{}{}", self.endpoint, self.path), &[])
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let marks = self
            .http
            .get_json_with_query(&format!("{}/eapi/v1/mark", self.endpoint), &[])
            .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        let mark_by_symbol = marks
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|value| {
                value
                    .get("symbol")
                    .and_then(Value::as_str)
                    .map(|symbol| (symbol.to_ascii_uppercase(), value))
            })
            .collect::<std::collections::HashMap<_, _>>();
        let mut events = Vec::new();
        for value in tickers.as_array().into_iter().flatten() {
            let Some(symbol) = value.get("symbol").and_then(Value::as_str) else {
                continue;
            };
            if !requested.contains(&symbol.to_ascii_uppercase()) {
                continue;
            }
            events.push(quote_event(value, symbol)?);
            if let Some(mark) = mark_by_symbol.get(&symbol.to_ascii_uppercase()) {
                if let Some(greeks) = greeks_event(mark, symbol) {
                    events.push(greeks);
                }
            }
        }
        Ok(events)
    }
}

pub type BinanceDerivativesRestMarketStream =
    RestPollingMarketStream<BinanceDerivativesSnapshotReader>;

pub fn rest_market_stream(
    endpoint: impl Into<String>,
    path: impl Into<String>,
    product: ConnectionDomain,
) -> Result<BinanceDerivativesRestMarketStream, IntegrationError> {
    let product_name = match product {
        ConnectionDomain::UsdMFutures => "usd-m-futures",
        ConnectionDomain::CoinMFutures => "coin-m-futures",
        ConnectionDomain::Options => "options",
        _ => "derivatives",
    };
    let identity = ConnectionDescriptor::new(
        format!("market.binance.{product_name}.rest-stream"),
        crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Exchange, "binance")
            .expect("static Binance participant"),
        product_name,
    )
    .map_err(IntegrationError::InvalidRequest)?;
    Ok(RestPollingMarketStream::new(
        identity,
        BinanceDerivativesSnapshotReader::new(endpoint, path, product)?,
    ))
}

/// Binance Options public WebSocket stream.
///
/// Option chain subscriptions are queued and sent as one combined SUBSCRIBE
/// request on the first poll, keeping the provider's inbound-message limit
/// intact even when the chain contains hundreds of contracts.
pub struct BinanceOptionsWebSocketMarketStream {
    identity: ConnectionDescriptor,
    state: ConnectionState,
    endpoint: String,
    socket: Option<TokioSocket>,
    pending_symbols: Vec<String>,
    subscriptions: BTreeMap<SubscriptionId, Vec<String>>,
    next_id: u64,
    request_id: u64,
    pending_events: VecDeque<MarketEvent>,
}

impl BinanceOptionsWebSocketMarketStream {
    pub fn new(endpoint: impl Into<String>) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into().trim_end_matches('/').to_owned();
        if !(endpoint.starts_with("wss://") || endpoint.starts_with("ws://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Options WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        let identity = ConnectionDescriptor::new(
            "market.binance.options.websocket",
            crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Exchange, "binance")
                .expect("static Binance participant"),
            "options",
        )
        .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(identity.clone()),
            identity,
            endpoint,
            socket: None,
            pending_symbols: Vec::new(),
            subscriptions: BTreeMap::new(),
            next_id: 1,
            request_id: 1,
            pending_events: VecDeque::new(),
        })
    }

    fn open(&mut self) -> Result<(), String> {
        self.socket = Some(TokioSocket::connect(self.endpoint.clone())?);
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos().into());
        self.state.last_error = None;
        Ok(())
    }

    fn flush_pending(&mut self) -> Result<(), String> {
        if self.pending_symbols.is_empty() {
            return Ok(());
        }
        let symbols = std::mem::take(&mut self.pending_symbols);
        let params = symbols
            .iter()
            .map(|symbol| format!("{}@bookTicker", symbol.to_ascii_lowercase()))
            .collect::<Vec<_>>();
        let message = serde_json::json!({
            "method": "SUBSCRIBE",
            "params": params,
            "id": self.request_id,
        });
        self.request_id = self.request_id.saturating_add(1);
        self.socket
            .as_ref()
            .ok_or_else(|| "Binance Options WebSocket is not connected".to_string())?
            .send_text(message.to_string())
    }

    fn parse_event(payload: &str) -> Result<Option<MarketEvent>, String> {
        let mut value: Value = serde_json::from_str(payload).map_err(|error| error.to_string())?;
        if let Some(data) = value.get("data").cloned() {
            value = data;
        }
        if value.get("result").is_some() || value.get("id").is_some() && value.get("e").is_none() {
            return Ok(None);
        }
        let Some(symbol) = value.get("s").and_then(Value::as_str) else {
            return Ok(None);
        };
        let bid = text(&value, &["b", "bidPrice"]);
        let ask = text(&value, &["a", "askPrice"]);
        if bid.is_none() && ask.is_none() {
            return Ok(None);
        }
        let observed_at_unix_nanos = value
            .get("E")
            .and_then(Value::as_u64)
            .map(|milliseconds| milliseconds.saturating_mul(1_000_000))
            .unwrap_or_else(now_unix_nanos);
        Ok(Some(MarketEvent {
            symbol: Symbol::new(symbol.to_ascii_uppercase()).map_err(|error| error.to_string())?,
            kind: MarketEventKind::Quote,
            price: parse_optional::<Price>(bid.clone().or_else(|| ask.clone()))
                .map_err(|error| error.to_string())?,
            quantity: parse_optional::<Quantity>(text(&value, &["B", "bidQty"]))
                .map_err(|error| error.to_string())?,
            rate: None,
            ask_price: parse_optional::<Price>(ask).map_err(|error| error.to_string())?,
            ask_quantity: parse_optional::<Quantity>(text(&value, &["A", "askQty"]))
                .map_err(|error| error.to_string())?,
            bids: Vec::new(),
            asks: Vec::new(),
            bar: None,
            greeks: None,
            first_sequence: None,
            last_sequence: None,
            sequence: value.get("u").and_then(Value::as_u64).map(Sequence::new),
            observed_at_unix_nanos: observed_at_unix_nanos.into(),
        }))
    }
}

impl MarketStreamConnection for BinanceOptionsWebSocketMarketStream {
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
        self.pending_symbols.clear();
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
        let id = SubscriptionId(self.next_id);
        self.next_id = self.next_id.saturating_add(1);
        self.pending_symbols.extend(request.symbols.iter().cloned());
        self.subscriptions.insert(id, request.symbols);
        Ok(id)
    }

    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        let symbols = self.subscriptions.remove(&subscription).ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown market subscription".into())
        })?;
        let params = symbols
            .iter()
            .map(|symbol| format!("{}@bookTicker", symbol.to_ascii_lowercase()))
            .collect::<Vec<_>>();
        let message =
            serde_json::json!({"method":"UNSUBSCRIBE","params":params,"id":self.request_id});
        self.request_id = self.request_id.saturating_add(1);
        self.socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .send_text(message.to_string())
            .map_err(IntegrationError::Transport)
    }

    fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        self.flush_pending().map_err(IntegrationError::Transport)?;
        if let Some(event) = self.pending_events.pop_front() {
            return Ok(Some(event));
        }
        let event = self
            .socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .try_recv()
            .map_err(IntegrationError::Transport)?;
        let Some(event) = event else { return Ok(None) };
        let message = match event {
            SocketEvent::Message(message) => message,
            SocketEvent::Error(error) => {
                self.state.lifecycle = ConnectionLifecycle::Degraded;
                return Err(IntegrationError::Transport(error));
            }
            SocketEvent::Backpressure => {
                self.state.lifecycle = ConnectionLifecycle::Degraded;
                return Err(IntegrationError::Backpressure(
                    "Binance derivatives market event queue overflowed".into(),
                ));
            }
        };
        match message {
            Message::Text(text) => {
                Self::parse_event(text.as_ref()).map_err(IntegrationError::InvalidPayload)
            }
            Message::Ping(payload) => {
                self.socket
                    .as_ref()
                    .unwrap()
                    .send_pong(payload.to_vec())
                    .map_err(IntegrationError::Transport)?;
                Ok(None)
            }
            Message::Close(_) => Err(IntegrationError::Transport(
                "Binance Options WebSocket closed".into(),
            )),
            Message::Binary(_) | Message::Pong(_) | Message::Frame(_) => Ok(None),
        }
    }
}

pub fn websocket_market_stream(
    endpoint: impl Into<String>,
) -> Result<BinanceOptionsWebSocketMarketStream, IntegrationError> {
    BinanceOptionsWebSocketMarketStream::new(endpoint)
}

fn quote_event(payload: &Value, fallback_symbol: &str) -> Result<MarketEvent, IntegrationError> {
    let symbol = payload
        .get("symbol")
        .or_else(|| payload.get("s"))
        .and_then(Value::as_str)
        .unwrap_or(fallback_symbol)
        .to_ascii_uppercase();
    let bid = text(payload, &["bidPrice", "b"]);
    let ask = text(payload, &["askPrice", "a"]);
    if bid.is_none() && ask.is_none() {
        return Err(IntegrationError::InvalidPayload(
            "Binance derivatives quote has neither bid nor ask".into(),
        ));
    }
    Ok(MarketEvent {
        symbol: Symbol::new(symbol)
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        kind: MarketEventKind::Quote,
        price: parse_optional::<Price>(bid.clone().or_else(|| ask.clone()))?,
        quantity: parse_optional::<Quantity>(text(payload, &["bidQty", "B"]))?,
        rate: None,
        ask_price: parse_optional::<Price>(ask)?,
        ask_quantity: parse_optional::<Quantity>(text(payload, &["askQty", "A"]))?,
        bids: Vec::new(),
        asks: Vec::new(),
        bar: None,
        greeks: None,
        first_sequence: None,
        last_sequence: None,
        sequence: None,
        observed_at_unix_nanos: now_unix_nanos().into(),
    })
}

fn greeks_event(payload: &Value, fallback_symbol: &str) -> Option<MarketEvent> {
    let delta = text(payload, &["delta"]);
    let gamma = text(payload, &["gamma"]);
    let vega = text(payload, &["vega"]);
    let theta = text(payload, &["theta"]);
    let implied_volatility = text(payload, &["markIV", "sigma", "impliedVolatility"]);
    if delta.is_none()
        && gamma.is_none()
        && vega.is_none()
        && theta.is_none()
        && implied_volatility.is_none()
    {
        return None;
    }
    let symbol = payload
        .get("symbol")
        .or_else(|| payload.get("s"))
        .and_then(Value::as_str)
        .unwrap_or(fallback_symbol)
        .to_ascii_uppercase();
    let strike = symbol.split('-').nth(2).map(str::to_owned);
    Some(MarketEvent {
        symbol: Symbol::new(symbol).ok()?,
        kind: crate::application::capabilities::MarketEventKind::Greeks,
        price: None,
        quantity: None,
        rate: None,
        ask_price: None,
        ask_quantity: None,
        bids: Vec::new(),
        asks: Vec::new(),
        bar: None,
        greeks: Some(MarketGreeks {
            expiry_unix_nanos: None,
            strike: strike.and_then(|value| value.parse::<Price>().ok()),
            delta: delta.and_then(|value| value.parse::<Rate>().ok()),
            gamma: gamma.and_then(|value| value.parse::<Rate>().ok()),
            vega: vega.and_then(|value| value.parse::<Rate>().ok()),
            theta: theta.and_then(|value| value.parse::<Rate>().ok()),
            implied_volatility: implied_volatility.and_then(|value| value.parse::<Rate>().ok()),
            derivation: "binance-options-mark".into(),
        }),
        first_sequence: None,
        last_sequence: None,
        sequence: None,
        observed_at_unix_nanos: now_unix_nanos().into(),
    })
}

fn text(payload: &Value, keys: &[&str]) -> Option<String> {
    keys.iter()
        .find_map(|key| payload.get(*key).and_then(Value::as_str).map(str::to_owned))
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

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn maps_binance_options_mark_to_greeks() {
        let event = greeks_event(
            &json!({
                "symbol": "BTC-260925-145000-C",
                "delta": "0.0005074",
                "gamma": "0.000000090",
                "vega": "0.41942711",
                "theta": "-0.37630817",
                "markIV": "0.654"
            }),
            "BTC-260925-145000-C",
        )
        .expect("greeks event");

        assert_eq!(event.kind, MarketEventKind::Greeks);
        assert_eq!(event.symbol, "BTC-260925-145000-C");
        let greeks = event.greeks.expect("greeks payload");
        assert_eq!(
            greeks.delta.map(|value| value.to_string()),
            Some("0.0005074".into())
        );
        assert_eq!(
            greeks.strike.map(|value| value.to_string()),
            Some("145000".into())
        );
        assert_eq!(
            greeks.implied_volatility.map(|value| value.to_string()),
            Some("0.654".into())
        );
        assert_eq!(greeks.derivation, "binance-options-mark");
    }
}
