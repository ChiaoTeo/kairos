//! OKX public WebSocket market projection.

use std::collections::BTreeMap;

use kairos_primitives::{Price, Quantity, Sequence, Symbol, UnixNanos};
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

use crate::application::{
    AsyncMarketEventSource, ConnectionDescriptor, IntegrationError, MarketEvent, MarketEventKind,
    MarketSubscription, SubscriptionId,
};
use crate::domain::{ConnectionHealth, ConnectionState};
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};

pub struct OkxLiveMarket {
    state: ConnectionState,
    websocket_url: String,
    socket: Option<AsyncTokioSocket>,
    subscriptions: BTreeMap<SubscriptionId, Vec<String>>,
    sequences: BTreeMap<String, u64>,
    next_subscription_id: u64,
}

impl OkxLiveMarket {
    pub(super) fn new(
        descriptor: ConnectionDescriptor,
        websocket_url: String,
    ) -> Result<Self, IntegrationError> {
        if !(websocket_url.starts_with("ws://") || websocket_url.starts_with("wss://")) {
            return Err(IntegrationError::InvalidRequest(
                "OKX public WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        Ok(Self {
            state: ConnectionState::new(descriptor),
            websocket_url,
            socket: None,
            subscriptions: BTreeMap::new(),
            sequences: BTreeMap::new(),
            next_subscription_id: 1,
        })
    }

    async fn send_subscription(
        &self,
        operation: &str,
        symbols: &[String],
    ) -> Result<(), IntegrationError> {
        let args = symbols
            .iter()
            .flat_map(|symbol| {
                [
                    json!({"channel": "books", "instId": symbol}),
                    json!({"channel": "trades", "instId": symbol}),
                ]
            })
            .collect::<Vec<_>>();
        self.socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .send_text(json!({"op": operation, "args": args}).to_string())
            .await
            .map_err(IntegrationError::Transport)
    }

    async fn next_event(&mut self) -> Result<MarketEvent, IntegrationError> {
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
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
                    if value.get("event").is_some() {
                        continue;
                    }
                    let channel = value
                        .pointer("/arg/channel")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let symbol = value
                        .pointer("/arg/instId")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let Some(row) = value
                        .get("data")
                        .and_then(Value::as_array)
                        .and_then(|rows| rows.first())
                    else {
                        continue;
                    };
                    let event = match channel {
                        "books" => self.normalize_book(
                            symbol,
                            value.get("action").and_then(Value::as_str),
                            row,
                        )?,
                        "trades" => normalize_trade(symbol, row)?,
                        _ => continue,
                    };
                    return Ok(event);
                }
                AsyncSocketEvent::Message(Message::Ping(payload)) => {
                    self.socket
                        .as_ref()
                        .ok_or(IntegrationError::NotReady)?
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                }
                AsyncSocketEvent::Message(Message::Close(_)) => {
                    return Err(IntegrationError::Transport("OKX WebSocket closed".into()))
                }
                AsyncSocketEvent::Message(_) => {}
                AsyncSocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
                AsyncSocketEvent::Backpressure => {
                    return Err(IntegrationError::Backpressure(
                        "OKX WebSocket event queue overflowed".into(),
                    ))
                }
            }
        }
    }

    fn normalize_book(
        &mut self,
        symbol: &str,
        action: Option<&str>,
        row: &Value,
    ) -> Result<MarketEvent, IntegrationError> {
        let sequence = integer(row, "seqId")?;
        let previous = row
            .get("prevSeqId")
            .and_then(|value| {
                value
                    .as_i64()
                    .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
            })
            .unwrap_or(-1);
        let snapshot = action == Some("snapshot") || previous < 0;
        if !snapshot {
            if let Some(expected) = self.sequences.get(symbol).copied() {
                if previous as u64 != expected && sequence != expected {
                    self.sequences.remove(symbol);
                    return Err(IntegrationError::ResyncRequired(format!(
                        "OKX books gap for {symbol}: expected prevSeqId {expected}, got {previous}"
                    )));
                }
            }
        }
        self.sequences.insert(symbol.to_owned(), sequence);
        Ok(MarketEvent {
            symbol: parse_symbol(symbol)?,
            kind: if snapshot {
                MarketEventKind::BookSnapshot
            } else {
                MarketEventKind::BookDelta
            },
            price: None,
            quantity: None,
            rate: None,
            ask_price: None,
            ask_quantity: None,
            bids: levels(row.get("bids"))?,
            asks: levels(row.get("asks"))?,
            bar: None,
            greeks: None,
            first_sequence: Some(Sequence::new(if snapshot {
                sequence
            } else {
                previous.max(0) as u64 + 1
            })),
            last_sequence: Some(Sequence::new(sequence)),
            sequence: Some(Sequence::new(sequence)),
            observed_at_unix_nanos: timestamp(row)?,
            venue: Default::default(),
        })
    }
}

impl AsyncMarketEventSource for OkxLiveMarket {
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
            self.send_subscription("subscribe", symbols).await?;
        }
        self.state.mark_ready(false);
        Ok(())
    }
    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.sequences.clear();
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
        self.send_subscription("subscribe", &request.symbols)
            .await?;
        self.subscriptions.insert(id, request.symbols);
        Ok(id)
    }
    async fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        let symbols = self
            .subscriptions
            .remove(&subscription)
            .ok_or_else(|| IntegrationError::InvalidRequest("unknown OKX subscription".into()))?;
        self.send_subscription("unsubscribe", &symbols).await
    }
    async fn next_market_event(&mut self) -> Result<MarketEvent, IntegrationError> {
        self.next_event().await
    }
}

fn normalize_trade(symbol: &str, row: &Value) -> Result<MarketEvent, IntegrationError> {
    Ok(MarketEvent {
        symbol: parse_symbol(symbol)?,
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
        sequence: row
            .get("tradeId")
            .and_then(Value::as_str)
            .and_then(|v| v.parse().ok())
            .map(Sequence::new),
        observed_at_unix_nanos: timestamp(row)?,
        venue: Default::default(),
    })
}

fn levels(value: Option<&Value>) -> Result<Vec<(Price, Quantity)>, IntegrationError> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|level| {
            let values = level.as_array().ok_or_else(|| {
                IntegrationError::InvalidPayload("OKX book level must be an array".into())
            })?;
            let price = values
                .first()
                .and_then(Value::as_str)
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload("OKX book level has no price".into())
                })?
                .parse()
                .map_err(|e| IntegrationError::InvalidPayload(format!("OKX price: {e}")))?;
            let quantity = values
                .get(1)
                .and_then(Value::as_str)
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload("OKX book level has no size".into())
                })?
                .parse()
                .map_err(|e| IntegrationError::InvalidPayload(format!("OKX size: {e}")))?;
            Ok((price, quantity))
        })
        .collect()
}

fn decimal<T: std::str::FromStr>(row: &Value, key: &str) -> Result<T, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    row.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("OKX payload has no {key}")))?
        .parse()
        .map_err(|e| IntegrationError::InvalidPayload(format!("OKX {key}: {e}")))
}
fn integer(row: &Value, key: &str) -> Result<u64, IntegrationError> {
    row.get(key)
        .and_then(|v| {
            v.as_i64()
                .or_else(|| v.as_str().and_then(|v| v.parse().ok()))
        })
        .and_then(|v| u64::try_from(v).ok())
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("OKX payload has invalid {key}")))
}
fn timestamp(row: &Value) -> Result<UnixNanos, IntegrationError> {
    Ok(UnixNanos::new(
        integer(row, "ts")?.saturating_mul(1_000_000),
    ))
}
fn parse_symbol(value: &str) -> Result<Symbol, IntegrationError> {
    Symbol::new(value).map_err(|e| IntegrationError::InvalidPayload(e.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::application::capabilities::{ParticipantKind, ParticipantRef};
    use crate::application::ConnectionDomainRef;
    fn market() -> OkxLiveMarket {
        OkxLiveMarket::new(
            ConnectionDescriptor {
                binding_id: "test".into(),
                participant: ParticipantRef::new(ParticipantKind::Exchange, "okx").unwrap(),
                environment: "test".into(),
                principal_id: None,
                domain: ConnectionDomainRef::new("market-data").unwrap(),
            },
            "ws://127.0.0.1:1".into(),
        )
        .unwrap()
    }
    #[test]
    fn validates_incremental_sequence_and_requests_resync_on_gap() {
        let mut market = market();
        let snapshot =
            json!({"seqId": "10", "prevSeqId": "-1", "ts": "1", "bids": [["1","2"]], "asks": []});
        assert_eq!(
            market
                .normalize_book("BTC-USDT", Some("snapshot"), &snapshot)
                .unwrap()
                .kind,
            MarketEventKind::BookSnapshot
        );
        let update = json!({"seqId": "12", "prevSeqId": "11", "ts": "2", "bids": [], "asks": []});
        assert!(matches!(
            market.normalize_book("BTC-USDT", Some("update"), &update),
            Err(IntegrationError::ResyncRequired(_))
        ));
    }
}
