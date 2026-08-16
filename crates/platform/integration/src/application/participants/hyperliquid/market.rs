use std::collections::{BTreeMap, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::{Price, ProviderSymbol, Quantity, Sequence, Symbol, UnixNanos};
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

use crate::application::{
    AsyncMarketEventSource, AsyncMarketSnapshotConnection, ConnectionDescriptor, IntegrationError,
    MarketEvent, MarketEventKind, MarketSubscription, SubscriptionId,
};
use crate::domain::{ConnectionHealth, ConnectionState};
use crate::services::transport::http::AsyncPublicHttpClient;
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};

use super::connection::map_exchange_error;

/// Hyperliquid's read-only `allMids` Info operation. Market owns polling,
/// symbol selection and canonical market identity.
pub struct HyperliquidMarketSnapshot {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) endpoint: String,
    pub(super) client: AsyncPublicHttpClient,
}

/// Hyperliquid public live feed. `l2Book` messages are complete snapshots on
/// every push, as specified by the provider, so no synthetic delta sequence is
/// maintained.
pub struct HyperliquidLiveMarket {
    state: ConnectionState,
    websocket_url: String,
    socket: Option<AsyncTokioSocket>,
    subscriptions: BTreeMap<SubscriptionId, Vec<String>>,
    pending: VecDeque<MarketEvent>,
    next_subscription_id: u64,
}

impl HyperliquidLiveMarket {
    pub(super) fn new(
        descriptor: ConnectionDescriptor,
        websocket_url: String,
    ) -> Result<Self, IntegrationError> {
        if !(websocket_url.starts_with("ws://") || websocket_url.starts_with("wss://")) {
            return Err(IntegrationError::InvalidRequest(
                "Hyperliquid WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        Ok(Self {
            state: ConnectionState::new(descriptor),
            websocket_url,
            socket: None,
            subscriptions: BTreeMap::new(),
            pending: VecDeque::new(),
            next_subscription_id: 1,
        })
    }

    async fn send(&self, method: &str, symbols: &[String]) -> Result<(), IntegrationError> {
        let socket = self.socket.as_ref().ok_or(IntegrationError::NotReady)?;
        for symbol in symbols {
            for kind in ["l2Book", "trades"] {
                socket
                    .send_text(
                        json!({"method": method, "subscription": {"type": kind, "coin": symbol}})
                            .to_string(),
                    )
                    .await
                    .map_err(IntegrationError::Transport)?;
            }
        }
        Ok(())
    }

    async fn next_event(&mut self) -> Result<MarketEvent, IntegrationError> {
        if let Some(event) = self.pending.pop_front() {
            return Ok(event);
        }
        loop {
            match self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .next_event()
                .await
            {
                AsyncSocketEvent::Message(Message::Text(payload)) => {
                    let value: Value = serde_json::from_str(&payload)
                        .map_err(|e| IntegrationError::InvalidPayload(e.to_string()))?;
                    match value.get("channel").and_then(Value::as_str) {
                        Some("subscriptionResponse") | Some("pong") => continue,
                        Some("l2Book") => {
                            return normalize_book(value.get("data").ok_or_else(|| {
                                IntegrationError::InvalidPayload(
                                    "Hyperliquid l2Book has no data".into(),
                                )
                            })?)
                        }
                        Some("trades") => {
                            let rows =
                                value.get("data").and_then(Value::as_array).ok_or_else(|| {
                                    IntegrationError::InvalidPayload(
                                        "Hyperliquid trades data must be an array".into(),
                                    )
                                })?;
                            let mut events =
                                rows.iter()
                                    .map(normalize_trade)
                                    .collect::<Result<VecDeque<_>, _>>()?;
                            let Some(first) = events.pop_front() else {
                                continue;
                            };
                            self.pending.extend(events);
                            return Ok(first);
                        }
                        _ => continue,
                    }
                }
                AsyncSocketEvent::Message(Message::Ping(payload)) => self
                    .socket
                    .as_ref()
                    .ok_or(IntegrationError::NotReady)?
                    .send_pong(payload.to_vec())
                    .await
                    .map_err(IntegrationError::Transport)?,
                AsyncSocketEvent::Message(Message::Close(_)) => {
                    return Err(IntegrationError::Transport(
                        "Hyperliquid WebSocket closed".into(),
                    ))
                }
                AsyncSocketEvent::Message(_) => {}
                AsyncSocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
                AsyncSocketEvent::Backpressure => {
                    return Err(IntegrationError::Backpressure(
                        "Hyperliquid WebSocket event queue overflowed".into(),
                    ))
                }
            }
        }
    }
}

impl AsyncMarketEventSource for HyperliquidLiveMarket {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.socket.is_some() {
            return Ok(());
        }
        self.state.lifecycle = crate::domain::ConnectionLifecycle::Starting;
        self.socket = Some(
            AsyncTokioSocket::connect(&self.websocket_url, 4_096)
                .await
                .map_err(IntegrationError::Transport)?,
        );
        for symbols in self.subscriptions.values() {
            self.send("subscribe", symbols).await?;
        }
        self.state.mark_ready(false);
        Ok(())
    }
    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.pending.clear();
        self.state.mark_stopped();
        Ok(())
    }
    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.disconnect_channel().await?;
        self.connect_channel().await?;
        self.state.mark_reconnected(false);
        Ok(())
    }
    fn channel_health(&self) -> ConnectionHealth {
        self.state.health()
    }
    async fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.socket.is_none() {
            return Err(IntegrationError::NotReady);
        }
        let id = SubscriptionId(self.next_subscription_id);
        self.next_subscription_id += 1;
        self.send("subscribe", &request.symbols).await?;
        self.subscriptions.insert(id, request.symbols);
        Ok(id)
    }
    async fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        let symbols = self.subscriptions.remove(&subscription).ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown Hyperliquid subscription".into())
        })?;
        self.send("unsubscribe", &symbols).await
    }
    async fn next_market_event(&mut self) -> Result<MarketEvent, IntegrationError> {
        self.next_event().await
    }
}

fn normalize_book(row: &Value) -> Result<MarketEvent, IntegrationError> {
    let symbol = symbol(row)?;
    let time = integer(row, "time")?;
    let sides = row.get("levels").and_then(Value::as_array).ok_or_else(|| {
        IntegrationError::InvalidPayload("Hyperliquid l2Book levels must be an array".into())
    })?;
    Ok(MarketEvent {
        symbol,
        kind: MarketEventKind::BookSnapshot,
        price: None,
        quantity: None,
        rate: None,
        ask_price: None,
        ask_quantity: None,
        bids: levels(sides.first())?,
        asks: levels(sides.get(1))?,
        bar: None,
        greeks: None,
        first_sequence: Some(Sequence::new(time)),
        last_sequence: Some(Sequence::new(time)),
        sequence: Some(Sequence::new(time)),
        observed_at_unix_nanos: UnixNanos::new(time.saturating_mul(1_000_000)),
    })
}

fn normalize_trade(row: &Value) -> Result<MarketEvent, IntegrationError> {
    let time = integer(row, "time")?;
    Ok(MarketEvent {
        symbol: symbol(row)?,
        kind: MarketEventKind::Trade,
        price: Some(decimal::<Price>(row, "px")?),
        quantity: Some(decimal::<Quantity>(row, "sz")?),
        rate: None,
        ask_price: None,
        ask_quantity: None,
        bids: vec![],
        asks: vec![],
        bar: None,
        greeks: None,
        first_sequence: None,
        last_sequence: None,
        sequence: row.get("tid").and_then(Value::as_u64).map(Sequence::new),
        observed_at_unix_nanos: UnixNanos::new(time.saturating_mul(1_000_000)),
    })
}
fn levels(value: Option<&Value>) -> Result<Vec<(Price, Quantity)>, IntegrationError> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| Ok((decimal(row, "px")?, decimal(row, "sz")?)))
        .collect()
}
fn decimal<T: std::str::FromStr>(row: &Value, key: &str) -> Result<T, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    row.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload(format!("Hyperliquid payload has no {key}"))
        })?
        .parse()
        .map_err(|e| IntegrationError::InvalidPayload(format!("Hyperliquid {key}: {e}")))
}
fn integer(row: &Value, key: &str) -> Result<u64, IntegrationError> {
    row.get(key).and_then(Value::as_u64).ok_or_else(|| {
        IntegrationError::InvalidPayload(format!("Hyperliquid payload has no {key}"))
    })
}
fn symbol(row: &Value) -> Result<Symbol, IntegrationError> {
    Symbol::new(row.get("coin").and_then(Value::as_str).ok_or_else(|| {
        IntegrationError::InvalidPayload("Hyperliquid payload has no coin".into())
    })?)
    .map_err(|e| IntegrationError::InvalidPayload(e.to_string()))
}

#[cfg(test)]
mod live_tests {
    use super::{normalize_book, normalize_trade};
    use crate::application::MarketEventKind;
    use serde_json::json;

    #[test]
    fn l2_book_is_an_authoritative_snapshot_and_trades_are_independent() {
        let book = normalize_book(&json!({
            "coin": "BTC", "time": 1000,
            "levels": [[{"px":"100","sz":"2","n":1}], [{"px":"101","sz":"3","n":1}]]
        }))
        .unwrap();
        assert_eq!(book.kind, MarketEventKind::BookSnapshot);
        assert_eq!(book.bids.len(), 1);
        assert_eq!(book.asks.len(), 1);
        let trade =
            normalize_trade(&json!({"coin":"BTC","time":1001,"px":"100.5","sz":"0.2","tid":9}))
                .unwrap();
        assert_eq!(trade.kind, MarketEventKind::Trade);
        assert_eq!(trade.sequence.unwrap().get(), 9);
    }
}

impl HyperliquidMarketSnapshot {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncMarketSnapshotConnection for HyperliquidMarketSnapshot {
    async fn fetch_snapshot(
        &mut self,
        symbols: &[ProviderSymbol],
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        let payload = self
            .client
            .post_query_json_with_headers(&self.endpoint, &[], &json!({"type": "allMids"}))
            .await
            .map_err(map_exchange_error)?;
        let mids = payload.as_object().ok_or_else(|| {
            IntegrationError::InvalidPayload(
                "Hyperliquid allMids response must be an object".into(),
            )
        })?;
        let observed_at_unix_nanos = UnixNanos::new(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?
                .as_nanos()
                .min(u128::from(u64::MAX)) as u64,
        );
        symbols
            .iter()
            .map(|symbol| {
                let raw = mids
                    .get(symbol.as_str())
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| {
                        IntegrationError::InvalidPayload(format!(
                            "Hyperliquid allMids has no symbol {}",
                            symbol.as_str()
                        ))
                    })?;
                let price = raw.parse::<Price>().map_err(|error| {
                    IntegrationError::InvalidPayload(format!(
                        "Hyperliquid mid for {}: {error}",
                        symbol.as_str()
                    ))
                })?;
                Ok(MarketEvent {
                    symbol: Symbol::new(symbol.as_str())
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    kind: MarketEventKind::Snapshot,
                    price: Some(price),
                    quantity: None,
                    rate: None,
                    ask_price: Some(price),
                    ask_quantity: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                    bar: None,
                    greeks: None,
                    first_sequence: None,
                    last_sequence: None,
                    sequence: None,
                    observed_at_unix_nanos,
                })
            })
            .collect()
    }
}
