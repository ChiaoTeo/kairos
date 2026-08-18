//! Massive Stocks and Options WebSocket market streams.

use kairos_primitives::{ParticipantSymbol, Price, Quantity, Sequence};
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

use crate::transport::websocket::{SocketEvent, TokioSocket};
use crate::{
    Bar, HistoricalBarRequest, HistoricalWindow, IntegrationError, MarketBar, MarketEvent,
    MarketEventKind, MarketQuote, MarketTrade, MarketVenueEvidence,
};
use crate::{ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState};

/// Massive-native market family. This private service type deliberately does
/// not reuse the legacy cross-participant product taxonomy.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum MarketType {
    Equity,
    Option,
}

pub(crate) struct SocketService {
    state: ConnectionState,
    api_key: String,
    endpoint: String,
    socket: Option<TokioSocket>,
    event_capacity: usize,
}

pub(crate) fn normalize_historical_quotes(
    rows: Vec<crate::services::participants::massive::rest::MassiveHistoricalQuote>,
    window: &HistoricalWindow,
) -> Result<Vec<MarketQuote>, IntegrationError> {
    rows.into_iter()
        .map(|row| {
            Ok(MarketQuote {
                symbol: ParticipantSymbol::new(window.symbol.as_str())
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                bid_price: parse_optional::<Price>(row.bid_price)?,
                bid_quantity: parse_optional::<Quantity>(row.bid_size)?,
                ask_price: parse_optional::<Price>(row.ask_price)?,
                ask_quantity: parse_optional::<Quantity>(row.ask_size)?,
                last_price: None,
                observed_at_unix_nanos: row.sip_timestamp_unix_nanos.into(),
            })
        })
        .collect()
}

pub(crate) fn normalize_historical_trades(
    rows: Vec<crate::services::participants::massive::rest::MassiveHistoricalTrade>,
    window: &HistoricalWindow,
) -> Result<Vec<MarketTrade>, IntegrationError> {
    rows.into_iter()
        .map(|row| {
            Ok(MarketTrade {
                symbol: ParticipantSymbol::new(window.symbol.as_str())
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                participant_trade_id: None,
                price: parse_required::<Price>(Some(row.price))?,
                quantity: parse_required::<Quantity>(Some(row.size))?,
                is_buyer_maker: None,
                event_at_unix_nanos: row.sip_timestamp_unix_nanos.into(),
            })
        })
        .collect()
}

pub(crate) fn normalize_historical(
    rows: Vec<crate::services::participants::massive::rest::MassiveHistoricalBar>,
    request: &HistoricalBarRequest,
    interval: &str,
    market_type: MarketType,
) -> Result<Vec<MarketBar>, IntegrationError> {
    rows.into_iter()
        .map(|row| -> Result<MarketBar, IntegrationError> {
            Ok(MarketBar {
                symbol: ParticipantSymbol::new(request.window.symbol.as_str())
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                interval: interval.into(),
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
                opened_at_unix_nanos: ((row.open_time_unix_millis as u64) * 1_000_000).into(),
                closed_at_unix_nanos: None,
                adjusted: request.adjusted,
                derivation: match market_type {
                    MarketType::Equity => "massive-stocks-aggregate".into(),
                    MarketType::Option => "massive-options-aggregate".into(),
                },
            })
        })
        .collect()
}

pub(crate) fn parse_interval(value: &str) -> Result<(u32, &'static str), IntegrationError> {
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

impl SocketService {
    pub(crate) fn new(
        descriptor: ConnectionDescriptor,
        api_key: impl Into<String>,
        endpoint: impl Into<String>,
        event_capacity: usize,
    ) -> Result<Self, IntegrationError> {
        if event_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Massive event queue capacity must be positive".into(),
            ));
        }
        let endpoint = websocket_endpoint(endpoint.into())?;
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            state: ConnectionState::new(descriptor),
            api_key: api_key.into(),
            endpoint,
            socket: None,
            event_capacity,
        })
    }

    pub(crate) async fn send(&self, value: Value) -> Result<(), IntegrationError> {
        self.socket
            .as_ref()
            .ok_or(IntegrationError::NotReady)?
            .send_text(value.to_string())
            .await
            .map_err(IntegrationError::Transport)
    }

    pub(crate) async fn next_rows(&mut self) -> Result<Vec<Value>, IntegrationError> {
        loop {
            let event = self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .next_event()
                .await;
            let message = match event {
                SocketEvent::Message(message) => message,
                SocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
                SocketEvent::Backpressure => {
                    return Err(IntegrationError::Backpressure(
                        "Massive market event queue overflowed".into(),
                    ))
                }
            };
            match message {
                Message::Text(text) => {
                    let value: Value = serde_json::from_str(text.as_ref())
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
                    return Ok(value.as_array().cloned().unwrap_or_else(|| vec![value]));
                }
                Message::Ping(payload) => {
                    self.socket
                        .as_ref()
                        .expect("connected Massive socket")
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                }
                Message::Close(_) => {
                    return Err(IntegrationError::Transport(
                        "Massive market WebSocket closed".into(),
                    ))
                }
                _ => {}
            }
        }
    }
}

impl SocketService {
    pub(crate) async fn connect(&mut self) -> Result<(), IntegrationError> {
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
            TokioSocket::connect(&self.endpoint, self.event_capacity)
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

    pub(crate) async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        self.state.authenticated = false;
        Ok(())
    }

    pub(crate) fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        &self.state.identity
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

pub(crate) fn normalize(value: &Value) -> Result<Option<MarketEvent>, IntegrationError> {
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
            symbol: ParticipantSymbol::new(symbol)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            kind: MarketEventKind::Quote,
            price: parse_optional::<Price>(
                scalar_text(value, "bp").or_else(|| scalar_text(value, "ap")),
            )?,
            quantity: parse_optional::<Quantity>(scalar_text(value, "bs"))?,
            rate: None,
            ask_price: parse_optional::<Price>(scalar_text(value, "ap"))?,
            ask_quantity: parse_optional::<Quantity>(scalar_text(value, "as"))?,
            bids: Vec::new(),
            asks: Vec::new(),
            bar: None,
            greeks: None,
            first_sequence: None,
            last_sequence: None,
            sequence: value.get("q").and_then(Value::as_u64).map(Sequence::new),
            observed_at_unix_nanos: timestamp.into(),
            venue: MarketVenueEvidence {
                bid_exchange: scalar_text(value, "bx"),
                ask_exchange: scalar_text(value, "ax"),
                tape: value
                    .get("z")
                    .and_then(Value::as_u64)
                    .and_then(|value| u32::try_from(value).ok()),
                ..Default::default()
            },
        })),
        "T" => Ok(Some(MarketEvent {
            symbol: ParticipantSymbol::new(symbol)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            kind: MarketEventKind::Trade,
            price: Some(parse_required::<Price>(scalar_text(value, "p"))?),
            quantity: Some(parse_required::<Quantity>(scalar_text(value, "s"))?),
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
            venue: MarketVenueEvidence {
                trade_exchange: scalar_text(value, "x"),
                tape: value
                    .get("z")
                    .and_then(Value::as_u64)
                    .and_then(|value| u32::try_from(value).ok()),
                trf_id: value
                    .get("trfi")
                    .and_then(Value::as_u64)
                    .and_then(|value| u32::try_from(value).ok()),
                trf_timestamp_unix_nanos: value.get("trft").and_then(Value::as_u64).map(Into::into),
                ..Default::default()
            },
        })),
        "A" | "AM" => Ok(Some(MarketEvent {
            symbol: ParticipantSymbol::new(symbol)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            kind: MarketEventKind::Bar,
            price: None,
            quantity: None,
            rate: None,
            ask_price: None,
            ask_quantity: None,
            bids: Vec::new(),
            asks: Vec::new(),
            bar: Some(Bar {
                timeframe: if event == "A" { "1s" } else { "1m" }.into(),
                open: parse_required::<Price>(scalar_text(value, "o"))?,
                high: parse_required::<Price>(scalar_text(value, "h"))?,
                low: parse_required::<Price>(scalar_text(value, "l"))?,
                close: parse_required::<Price>(scalar_text(value, "c"))?,
                volume: parse_optional::<Quantity>(scalar_text(value, "v"))?,
                derivation: "participant".into(),
            }),
            greeks: None,
            first_sequence: None,
            last_sequence: None,
            sequence: value.get("q").and_then(Value::as_u64).map(Sequence::new),
            observed_at_unix_nanos: timestamp.into(),
            venue: MarketVenueEvidence::default(),
        })),
        _ => Ok(None),
    }
}

fn scalar_text(value: &Value, key: &str) -> Option<String> {
    match value.get(key)? {
        Value::String(value) => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    }
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
pub(crate) fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::normalize;
    use crate::MarketEventKind;
    use serde_json::json;

    #[test]
    fn official_numeric_quote_fields_are_preserved() {
        let event = normalize(&json!({
            "ev": "Q",
            "sym": "AAPL",
            "bp": 224.10,
            "bs": 2,
            "ap": 224.12,
            "as": 3,
            "bx": 301,
            "ax": 302,
            "z": 3,
            "t": 1_536_036_818_784_u64,
            "q": 7
        }))
        .unwrap()
        .unwrap();

        assert_eq!(event.kind, MarketEventKind::Quote);
        assert_eq!(event.price.unwrap().to_string(), "224.1");
        assert_eq!(event.quantity.unwrap().to_string(), "2");
        assert_eq!(event.ask_price.unwrap().to_string(), "224.12");
        assert_eq!(event.ask_quantity.unwrap().to_string(), "3");
        assert_eq!(event.venue.bid_exchange.as_deref(), Some("301"));
        assert_eq!(event.venue.ask_exchange.as_deref(), Some("302"));
        assert_eq!(event.venue.tape, Some(3));
    }

    #[test]
    fn numeric_trade_fields_are_preserved() {
        let event = normalize(&json!({
            "ev": "T",
            "sym": "AAPL",
            "p": 224.11,
            "s": 5,
            "x": 19,
            "z": 3,
            "trfi": 202,
            "trft": 1_536_036_818_780_u64,
            "t": 1_536_036_818_784_u64,
            "q": 8
        }))
        .unwrap()
        .unwrap();

        assert_eq!(event.kind, MarketEventKind::Trade);
        assert_eq!(event.price.unwrap().to_string(), "224.11");
        assert_eq!(event.quantity.unwrap().to_string(), "5");
        assert_eq!(event.venue.trade_exchange.as_deref(), Some("19"));
        assert_eq!(event.venue.tape, Some(3));
        assert_eq!(event.venue.trf_id, Some(202));
        assert_eq!(
            event
                .venue
                .trf_timestamp_unix_nanos
                .map(|value| value.get()),
            Some(1_536_036_818_780)
        );
    }
}
