use std::collections::VecDeque;

use kairos_primitives::{ParticipantSymbol, UnixNanos};
use serde_json::Value;

use crate::{Bar, IntegrationError, MarketDataKind, MarketEvent, MarketEventKind, MarketFeed};

pub(crate) fn stream_name(feed: &MarketFeed, family: &str) -> Result<String, IntegrationError> {
    if family == "stocks" && feed.kind == MarketDataKind::Trade {
        return Ok("price".into());
    }
    let symbol = feed.symbol.as_ref().ok_or_else(|| {
        IntegrationError::InvalidRequest(format!("Binance {:?} feed requires a symbol", feed.kind))
    })?;
    let symbol = if family == "stocks" {
        symbol.as_str().to_ascii_uppercase()
    } else {
        symbol.as_str().to_ascii_lowercase()
    };
    let channel = match (family, feed.kind) {
        ("stocks", MarketDataKind::Quote) => "quote".into(),
        ("stocks", MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar) => {
            format!("kline_{}", feed.interval.as_deref().unwrap_or("1m"))
        }
        ("stocks", MarketDataKind::InstrumentStatus) => "tradingStatus".into(),
        (_, MarketDataKind::Quote) => "bookTicker".into(),
        (_, MarketDataKind::Ticker24h) => "ticker".into(),
        (_, MarketDataKind::Trade) => "trade".into(),
        (_, MarketDataKind::OrderBook) => match feed.update_speed_millis {
            Some(100) => "depth@100ms".into(),
            _ => "depth".into(),
        },
        (_, MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar) => {
            format!("kline_{}", feed.interval.as_deref().unwrap_or("1m"))
        }
        (_, MarketDataKind::MarkPrice) => "markPrice".into(),
        (_, MarketDataKind::IndexPrice) => "indexPrice".into(),
        (_, MarketDataKind::FundingRate) => "markPrice".into(),
        (_, unsupported) => {
            return Err(IntegrationError::InvalidRequest(format!(
                "Binance {family} WebSocket does not support {unsupported:?}"
            )))
        }
    };
    Ok(format!("{symbol}@{channel}"))
}

pub(crate) fn normalize(value: &Value) -> Result<VecDeque<MarketEvent>, IntegrationError> {
    if value.get("result").is_some() || value.get("id").is_some() && value.get("e").is_none() {
        return Ok(VecDeque::new());
    }
    let value = value.get("data").unwrap_or(value);
    let event_name = text(value, "e")
        .or_else(|| text(value, "eventType"))
        .unwrap_or_default();
    let symbol = text(value, "s")
        .or_else(|| text(value, "symbol"))
        .or_else(|| text(value, "ticker"));
    let Some(symbol) = symbol else {
        return Ok(VecDeque::new());
    };
    let observed = millis(
        value
            .get("E")
            .or_else(|| value.get("T"))
            .or_else(|| value.get("timestamp")),
    );
    let mut event = empty(symbol, observed)?;
    match event_name {
        "trade" | "aggTrade" | "price" => {
            event.kind = MarketEventKind::Trade;
            event.price = parse(value.get("p").or_else(|| value.get("price")))?;
            event.quantity = parse(value.get("q").or_else(|| value.get("quantity")))?;
        }
        "depthUpdate" | "depth" => {
            event.kind = MarketEventKind::BookDelta;
            event.bids = levels(value.get("b").or_else(|| value.get("bids")))?;
            event.asks = levels(value.get("a").or_else(|| value.get("asks")))?;
            event.first_sequence = value.get("U").and_then(Value::as_u64).map(Into::into);
            event.last_sequence = value.get("u").and_then(Value::as_u64).map(Into::into);
        }
        "kline" => normalize_kline(&mut event, value.get("k").unwrap_or(value))?,
        "markPriceUpdate" => {
            event.kind = MarketEventKind::MarkPrice;
            event.price = parse(value.get("p"))?;
            event.rate = parse(value.get("r"))?;
        }
        "indexPriceUpdate" => {
            event.kind = MarketEventKind::IndexPrice;
            event.price = parse(value.get("p"))?;
        }
        "tradingStatus" => {
            event.kind = MarketEventKind::InstrumentStatus;
        }
        _ => {
            event.kind = MarketEventKind::Quote;
            event.price = parse(
                value
                    .get("c")
                    .or_else(|| value.get("lastPrice"))
                    .or_else(|| value.get("price")),
            )?;
            event.ask_price = parse(value.get("a").or_else(|| value.get("askPrice")))?;
            event.ask_price = event.ask_price.or(parse(value.get("ap"))?);
            event.ask_quantity = parse(
                value
                    .get("A")
                    .or_else(|| value.get("askQty"))
                    .or_else(|| value.get("as")),
            )?;
            if let (Some(price), Some(quantity)) = (
                parse(
                    value
                        .get("b")
                        .or_else(|| value.get("bidPrice"))
                        .or_else(|| value.get("bp")),
                )?,
                parse(
                    value
                        .get("B")
                        .or_else(|| value.get("bidQty"))
                        .or_else(|| value.get("bs")),
                )?,
            ) {
                event.bids.push((price, quantity));
            }
        }
    }
    Ok(VecDeque::from([event]))
}

fn normalize_kline(event: &mut MarketEvent, row: &Value) -> Result<(), IntegrationError> {
    event.kind = MarketEventKind::Bar;
    event.bar = Some(Bar {
        timeframe: text(row, "i")
            .or_else(|| text(row, "interval"))
            .unwrap_or("unknown")
            .into(),
        open: required(row.get("o").or_else(|| row.get("open")))?,
        high: required(row.get("h").or_else(|| row.get("high")))?,
        low: required(row.get("l").or_else(|| row.get("low")))?,
        close: required(row.get("c").or_else(|| row.get("close")))?,
        volume: parse(row.get("v").or_else(|| row.get("volume")))?,
        derivation: "participant".into(),
    });
    Ok(())
}

fn empty(symbol: &str, observed_at_unix_nanos: UnixNanos) -> Result<MarketEvent, IntegrationError> {
    Ok(MarketEvent {
        symbol: ParticipantSymbol::new(symbol).map_err(payload)?,
        kind: MarketEventKind::Heartbeat,
        price: None,
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
        observed_at_unix_nanos,
        venue: Default::default(),
    })
}

fn levels(
    value: Option<&Value>,
) -> Result<Vec<(kairos_primitives::Price, kairos_primitives::Quantity)>, IntegrationError> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|level| {
            let level = level.as_array().ok_or_else(|| {
                IntegrationError::InvalidPayload("Binance book level must be an array".into())
            })?;
            Ok((required(level.first())?, required(level.get(1))?))
        })
        .collect()
}

fn text<'a>(value: &'a Value, field: &str) -> Option<&'a str> {
    value.get(field).and_then(Value::as_str)
}

fn parse<T: std::str::FromStr>(value: Option<&Value>) -> Result<Option<T>, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    value
        .and_then(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .or_else(|| value.as_f64().map(|value| value.to_string()))
        })
        .filter(|value| !value.is_empty())
        .map(|value| value.parse())
        .transpose()
        .map_err(payload)
}

fn required<T: std::str::FromStr>(value: Option<&Value>) -> Result<T, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    parse(value)?.ok_or_else(|| {
        IntegrationError::InvalidPayload("Binance numeric stream field is missing".into())
    })
}

fn millis(value: Option<&Value>) -> UnixNanos {
    let millis = value
        .and_then(|value| {
            value
                .as_u64()
                .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
        })
        .unwrap_or_default();
    UnixNanos::from(millis.saturating_mul(1_000_000))
}

fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}
