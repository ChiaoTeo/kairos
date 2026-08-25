use std::collections::VecDeque;

use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::time::UnixNanos;
use serde_json::Value;

use crate::{
    Bar, Greeks, IntegrationError, MarketDataKind, MarketEvent, MarketEventKind, MarketFeed,
};

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
        },
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
        },
        (_, MarketDataKind::MarkPrice) => "markPrice".into(),
        (_, MarketDataKind::IndexPrice) => "indexPrice".into(),
        (_, MarketDataKind::FundingRate) => "markPrice".into(),
        (_, unsupported) => {
            return Err(IntegrationError::InvalidRequest(format!(
                "Binance {family} WebSocket does not support {unsupported:?}"
            )));
        },
    };
    Ok(format!("{symbol}@{channel}"))
}

pub(crate) fn normalize(value: &Value) -> Result<VecDeque<MarketEvent>, IntegrationError> {
    if let Some(values) = value.as_array() {
        let mut events = VecDeque::new();
        for value in values {
            events.extend(normalize(value)?);
        }
        return Ok(events);
    }
    if value.get("result").is_some() || value.get("id").is_some() && value.get("e").is_none() {
        return Ok(VecDeque::new());
    }
    if let Some(data) = value.get("data") {
        return normalize(data);
    }
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
    let option_ticker = event_name == "24hrTicker"
        && ["d", "g", "v", "vo"]
            .into_iter()
            .any(|field| value.get(field).is_some());
    match event_name {
        "trade" | "aggTrade" | "price" => {
            event.kind = MarketEventKind::Trade;
            event.price = parse(value.get("p").or_else(|| value.get("price")))?;
            event.quantity = parse(value.get("q").or_else(|| value.get("quantity")))?;
        },
        "depthUpdate" | "depth" => {
            event.kind = MarketEventKind::BookDelta;
            event.bids = levels(value.get("b").or_else(|| value.get("bids")))?;
            event.asks = levels(value.get("a").or_else(|| value.get("asks")))?;
            event.first_sequence = value
                .get("U")
                .and_then(Value::as_u64)
                .or_else(|| {
                    value
                        .get("pu")
                        .and_then(Value::as_u64)
                        .map(|previous| previous.saturating_add(1))
                })
                .map(Into::into);
            event.last_sequence = value.get("u").and_then(Value::as_u64).map(Into::into);
            event.sequence = event.last_sequence;
        },
        "kline" => normalize_kline(&mut event, value.get("k").unwrap_or(value))?,
        "markPriceUpdate" | "optionMarkPrice" | "markPrice" => {
            event.kind = MarketEventKind::MarkPrice;
            event.price = parse(value.get("p").or_else(|| value.get("mp")))?;
            event.rate = parse(value.get("r"))?;
        },
        "indexPriceUpdate" => {
            event.kind = MarketEventKind::IndexPrice;
            event.price = parse(value.get("p"))?;
        },
        "tradingStatus" => {
            event.kind = MarketEventKind::InstrumentStatus;
        },
        _ => {
            event.kind = MarketEventKind::Quote;
            event.price = parse(
                value
                    .get("c")
                    .or_else(|| value.get("lastPrice"))
                    .or_else(|| value.get("mp"))
                    .or_else(|| value.get("price")),
            )?;
            event.ask_price = parse(if option_ticker {
                value.get("ao")
            } else {
                value.get("a").or_else(|| value.get("askPrice"))
            })?;
            event.ask_price = event.ask_price.or(parse(value.get("ap"))?);
            event.ask_quantity = parse(if option_ticker {
                value.get("aq")
            } else {
                value
                    .get("A")
                    .or_else(|| value.get("askQty"))
                    .or_else(|| value.get("as"))
            })?;
            if let (Some(price), Some(quantity)) = (
                parse(if option_ticker {
                    value.get("bo")
                } else {
                    value
                        .get("b")
                        .or_else(|| value.get("bidPrice"))
                        .or_else(|| value.get("bp"))
                })?,
                parse(if option_ticker {
                    value.get("bq")
                } else {
                    value
                        .get("B")
                        .or_else(|| value.get("bidQty"))
                        .or_else(|| value.get("bs"))
                })?,
            ) {
                event.bids.push((price, quantity));
            }
        },
    }
    let option_mark = event_name == "markPrice"
        && ["d", "g", "v", "vo"]
            .into_iter()
            .any(|field| value.get(field).is_some());
    if option_mark {
        let mut greeks = empty(symbol, observed)?;
        greeks.kind = MarketEventKind::Greeks;
        greeks.price = parse(value.get("mp"))?;
        greeks.greeks = Some(option_greeks(value)?);
        return Ok(VecDeque::from([event, greeks]));
    }
    if event_name == "markPriceUpdate" && value.get("r").is_some() {
        let mut funding = event.clone();
        funding.kind = MarketEventKind::FundingRate;
        funding.price = None;
        return Ok(VecDeque::from([event, funding]));
    }
    if event_name != "24hrTicker" {
        return Ok(VecDeque::from([event]));
    }
    if !option_ticker {
        event.kind = MarketEventKind::Ticker24h;
        return Ok(VecDeque::from([event]));
    }
    let mut ticker = event.clone();
    ticker.kind = MarketEventKind::Ticker24h;
    let mut greeks = empty(symbol, observed)?;
    greeks.kind = MarketEventKind::Greeks;
    greeks.price = parse(value.get("mp"))?;
    greeks.greeks = Some(option_greeks(value)?);
    Ok(VecDeque::from([event, ticker, greeks]))
}

fn option_greeks(value: &Value) -> Result<Greeks, IntegrationError> {
    Ok(Greeks {
        expiry_unix_nanos: None,
        strike: None,
        delta: parse(value.get("d"))?,
        gamma: parse(value.get("g"))?,
        vega: parse(value.get("v"))?,
        theta: parse(value.get("t"))?,
        implied_volatility: parse(value.get("vo"))?,
        derivation: "participant".into(),
    })
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
) -> Result<
    Vec<(
        kairos_primitives::decimal::Price,
        kairos_primitives::decimal::Quantity,
    )>,
    IntegrationError,
> {
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn depth_fixture_preserves_spot_and_futures_sequence_evidence() {
        let spot = normalize(&json!({
            "e":"depthUpdate","E":1,"s":"BTCUSDT","U":41,"u":42,
            "b":[["60000","1"]],"a":[["60001","2"]]
        }))
        .unwrap()
        .pop_front()
        .unwrap();
        let futures = normalize(&json!({
            "e":"depthUpdate","E":1,"s":"BTCUSDT","pu":42,"u":44,
            "b":[["60000","1"]],"a":[["60001","2"]]
        }))
        .unwrap()
        .pop_front()
        .unwrap();

        assert_eq!(spot.first_sequence.unwrap().get(), 41);
        assert_eq!(spot.last_sequence.unwrap().get(), 42);
        assert_eq!(futures.first_sequence.unwrap().get(), 43);
        assert_eq!(futures.last_sequence.unwrap().get(), 44);
    }

    #[test]
    fn options_ticker_emits_quote_ticker_and_greeks_observations() {
        let events = normalize(&json!({
            "e":"24hrTicker","E":1,"s":"BTC-260925-100000-C",
            "c":"100","bo":"99","bq":"2","ao":"101","aq":"3",
            "mp":"100.5","d":"0.5","g":"0.01","v":"12","t":"-4","vo":"0.6"
        }))
        .unwrap();

        assert_eq!(events.len(), 3);
        assert_eq!(events[0].kind, MarketEventKind::Quote);
        assert_eq!(events[1].kind, MarketEventKind::Ticker24h);
        assert_eq!(events[2].kind, MarketEventKind::Greeks);
        assert_eq!(events[0].ask_price.as_ref().unwrap().to_string(), "101");
        let values = events[2].greeks.as_ref().unwrap();
        assert_eq!(values.delta.as_ref().unwrap().to_string(), "0.5");
        assert_eq!(
            values.implied_volatility.as_ref().unwrap().to_string(),
            "0.6"
        );
    }

    #[test]
    fn options_mark_price_array_emits_mark_and_greeks_per_contract() {
        let events = normalize(&json!([{
            "s":"BTC-260925-100000-C","mp":"100.5","E":1,"e":"markPrice",
            "d":"0.5","g":"0.01","v":"12","t":"-4","vo":"0.6"
        }]))
        .unwrap();

        assert_eq!(events.len(), 2);
        assert_eq!(events[0].kind, MarketEventKind::MarkPrice);
        assert_eq!(events[1].kind, MarketEventKind::Greeks);
        assert_eq!(events[0].price.as_ref().unwrap().to_string(), "100.5");
        assert_eq!(
            events[1]
                .greeks
                .as_ref()
                .unwrap()
                .delta
                .as_ref()
                .unwrap()
                .to_string(),
            "0.5"
        );
    }

    #[test]
    fn futures_mark_price_emits_funding_rate_from_the_shared_stream() {
        let events = normalize(&json!({
            "e":"markPriceUpdate","E":1,"s":"BTCUSDT",
            "p":"79000.5","r":"0.0001","T":2
        }))
        .unwrap();

        assert_eq!(events.len(), 2);
        assert_eq!(events[0].kind, MarketEventKind::MarkPrice);
        assert_eq!(events[1].kind, MarketEventKind::FundingRate);
        assert_eq!(events[1].rate.as_ref().unwrap().to_string(), "0.0001");
    }
}
