use std::collections::VecDeque;

use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::time::UnixNanos;
use serde_json::Value;

use crate::{
    Bar, Greeks, IntegrationError, MarketBar, MarketDataKind, MarketEvent, MarketEventKind,
    MarketFeed, MarketFundingRate, MarketGreeks, MarketIndexPrice, MarketMarkPrice,
    MarketOpenInterest, MarketOrderBook, MarketQuote, MarketTrade, MarketVenueEvidence,
};

pub(crate) fn quote(
    symbol: &ParticipantSymbol,
    row: &Value,
) -> Result<MarketQuote, IntegrationError> {
    Ok(MarketQuote {
        symbol: symbol.clone(),
        bid_price: optional(row, "bidPx")?,
        bid_quantity: optional(row, "bidSz")?,
        ask_price: optional(row, "askPx")?,
        ask_quantity: optional(row, "askSz")?,
        last_price: optional(row, "last")?,
        observed_at_unix_nanos: timestamp(row.get("ts")),
    })
}

pub(crate) fn trades(
    symbol: &ParticipantSymbol,
    value: &Value,
) -> Result<Vec<MarketTrade>, IntegrationError> {
    rows(value)?
        .iter()
        .map(|row| {
            Ok(MarketTrade {
                symbol: symbol.clone(),
                participant_trade_id: text(row, "tradeId").map(str::to_owned),
                price: required(row, "px")?,
                quantity: required(row, "sz")?,
                is_buyer_maker: text(row, "side").map(|side| side == "sell"),
                event_at_unix_nanos: timestamp(row.get("ts")),
            })
        })
        .collect()
}

pub(crate) fn bars(
    symbol: &ParticipantSymbol,
    interval: &str,
    value: &Value,
) -> Result<Vec<MarketBar>, IntegrationError> {
    rows(value)?
        .iter()
        .map(|row| {
            let row = row.as_array().ok_or_else(|| {
                IntegrationError::InvalidPayload("OKX candle row must be an array".into())
            })?;
            Ok(MarketBar {
                symbol: symbol.clone(),
                interval: interval.into(),
                opened_at_unix_nanos: timestamp(row.first()),
                open: required_value(row.get(1))?,
                high: required_value(row.get(2))?,
                low: required_value(row.get(3))?,
                close: required_value(row.get(4))?,
                volume: optional_value(row.get(5))?,
                closed_at_unix_nanos: None,
                adjusted: None,
                derivation: "participant".into(),
            })
        })
        .collect()
}

pub(crate) fn book(
    symbol: &ParticipantSymbol,
    value: &Value,
) -> Result<MarketOrderBook, IntegrationError> {
    let row = rows(value)?
        .first()
        .ok_or_else(|| IntegrationError::InvalidPayload("OKX order book data is missing".into()))?;
    Ok(MarketOrderBook {
        symbol: symbol.clone(),
        bids: levels(row.get("bids"))?,
        asks: levels(row.get("asks"))?,
        sequence: text(row, "seqId")
            .and_then(|value| value.parse::<u64>().ok())
            .map(Into::into),
        observed_at_unix_nanos: timestamp(row.get("ts")),
    })
}

pub(crate) fn mark_price(
    symbol: &ParticipantSymbol,
    value: &Value,
) -> Result<MarketMarkPrice, IntegrationError> {
    let row = first_row(value, "mark price")?;
    Ok(MarketMarkPrice {
        symbol: symbol.clone(),
        price: required(row, "markPx")?,
        observed_at_unix_nanos: timestamp(row.get("ts")),
    })
}

pub(crate) fn index_price(
    symbol: &ParticipantSymbol,
    value: &Value,
) -> Result<MarketIndexPrice, IntegrationError> {
    let row = first_row(value, "index price")?;
    Ok(MarketIndexPrice {
        symbol: symbol.clone(),
        price: required(row, "idxPx")?,
        observed_at_unix_nanos: timestamp(row.get("ts")),
    })
}

pub(crate) fn funding_rate(
    symbol: &ParticipantSymbol,
    value: &Value,
) -> Result<MarketFundingRate, IntegrationError> {
    let row = first_row(value, "funding rate")?;
    Ok(MarketFundingRate {
        symbol: symbol.clone(),
        rate: required(row, "fundingRate")?,
        next_funding_at_unix_nanos: text(row, "nextFundingTime")
            .and_then(|value| value.parse::<u64>().ok())
            .map(|value| UnixNanos::from(value.saturating_mul(1_000_000))),
        observed_at_unix_nanos: timestamp(row.get("ts").or_else(|| row.get("fundingTime"))),
    })
}

pub(crate) fn open_interest(
    symbol: &ParticipantSymbol,
    value: &Value,
) -> Result<MarketOpenInterest, IntegrationError> {
    let row = first_row(value, "open interest")?;
    Ok(MarketOpenInterest {
        symbol: symbol.clone(),
        quantity: required(row, "oi")?,
        observed_at_unix_nanos: timestamp(row.get("ts")),
    })
}

pub(crate) fn greeks(
    symbol: &ParticipantSymbol,
    value: &Value,
) -> Result<MarketGreeks, IntegrationError> {
    let row = rows(value)?
        .iter()
        .find(|row| text(row, "instId") == Some(symbol.as_str()))
        .ok_or_else(|| IntegrationError::InvalidPayload("OKX option summary is missing".into()))?;
    Ok(MarketGreeks {
        symbol: symbol.clone(),
        values: Greeks {
            expiry_unix_nanos: None,
            strike: optional(row, "stk")?,
            delta: optional(row, "delta")?,
            gamma: optional(row, "gamma")?,
            vega: optional(row, "vega")?,
            theta: optional(row, "theta")?,
            implied_volatility: optional(row, "markVol")?,
            derivation: "participant".into(),
        },
        observed_at_unix_nanos: timestamp(row.get("ts")),
    })
}

fn first_row<'a>(value: &'a Value, label: &str) -> Result<&'a Value, IntegrationError> {
    rows(value)?
        .first()
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("OKX {label} data is missing")))
}

pub(crate) fn feed_argument(feed: &MarketFeed) -> Result<Value, IntegrationError> {
    let symbol = feed.symbol.as_ref().ok_or_else(|| {
        IntegrationError::InvalidRequest(format!("OKX {:?} feed requires a symbol", feed.kind))
    })?;
    let channel = match feed.kind {
        MarketDataKind::Quote | MarketDataKind::Ticker24h => "tickers".into(),
        MarketDataKind::Trade => "trades".into(),
        MarketDataKind::OrderBook => match feed.depth {
            Some(depth) if depth <= 5 => "books5".into(),
            _ => "books".into(),
        },
        MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar => {
            format!("candle{}", feed.interval.as_deref().unwrap_or("1m"))
        },
        MarketDataKind::MarkPrice => "mark-price".into(),
        MarketDataKind::IndexPrice => "index-tickers".into(),
        MarketDataKind::FundingRate => "funding-rate".into(),
        MarketDataKind::OpenInterest => "open-interest".into(),
        MarketDataKind::Greeks => "opt-summary".into(),
        MarketDataKind::InstrumentStatus => "status".into(),
    };
    Ok(serde_json::json!({"channel": channel, "instId": symbol.as_str()}))
}

pub(crate) fn stream_events(value: &Value) -> Result<VecDeque<MarketEvent>, IntegrationError> {
    if value.get("event").is_some() {
        return Ok(VecDeque::new());
    }
    let channel = value
        .pointer("/arg/channel")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let argument_symbol = value.pointer("/arg/instId").and_then(Value::as_str);
    value
        .get("data")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| stream_event(channel, argument_symbol, value, row))
        .collect()
}

fn stream_event(
    channel: &str,
    argument_symbol: Option<&str>,
    envelope: &Value,
    row: &Value,
) -> Result<MarketEvent, IntegrationError> {
    let symbol = row
        .get("instId")
        .and_then(Value::as_str)
        .or(argument_symbol)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("OKX market event instrument is missing".into())
        })?;
    let mut event = empty_event(symbol, timestamp(row.get("ts")))?;
    match channel {
        "trades" | "trades-all" => {
            event.kind = MarketEventKind::Trade;
            event.price = optional(row, "px")?;
            event.quantity = optional(row, "sz")?;
        },
        value if value.starts_with("books") => {
            event.kind = if envelope.get("action").and_then(Value::as_str) == Some("update") {
                MarketEventKind::BookDelta
            } else {
                MarketEventKind::BookSnapshot
            };
            event.bids = levels(row.get("bids"))?;
            event.asks = levels(row.get("asks"))?;
            event.first_sequence = integer(row.get("prevSeqId")).map(Into::into);
            event.last_sequence = integer(row.get("seqId")).map(Into::into);
            event.sequence = event.last_sequence;
        },
        value if value.starts_with("candle") => {
            let values = row.as_array().ok_or_else(|| {
                IntegrationError::InvalidPayload("OKX candle row must be an array".into())
            })?;
            let field = |index| {
                values
                    .get(index)
                    .and_then(Value::as_str)
                    .unwrap_or_default()
            };
            event.kind = MarketEventKind::Bar;
            event.observed_at_unix_nanos = timestamp(values.first());
            event.bar = Some(Bar {
                timeframe: value.trim_start_matches("candle").into(),
                open: field(1).parse().map_err(payload)?,
                high: field(2).parse().map_err(payload)?,
                low: field(3).parse().map_err(payload)?,
                close: field(4).parse().map_err(payload)?,
                volume: optional_value(values.get(5))?,
                derivation: "participant".into(),
            });
        },
        "mark-price" => {
            event.kind = MarketEventKind::MarkPrice;
            event.price = optional(row, "markPx")?;
        },
        "index-tickers" => {
            event.kind = MarketEventKind::IndexPrice;
            event.price = optional(row, "idxPx")?;
        },
        "funding-rate" => {
            event.kind = MarketEventKind::FundingRate;
            event.rate = optional(row, "fundingRate")?;
        },
        "open-interest" => {
            event.kind = MarketEventKind::OpenInterest;
            event.quantity = optional(row, "oi")?;
        },
        "opt-summary" => {
            event.kind = MarketEventKind::Greeks;
            event.greeks = Some(Greeks {
                expiry_unix_nanos: None,
                strike: optional(row, "stk")?,
                delta: optional(row, "delta")?,
                gamma: optional(row, "gamma")?,
                vega: optional(row, "vega")?,
                theta: optional(row, "theta")?,
                implied_volatility: optional(row, "markVol")?,
                derivation: "participant".into(),
            });
        },
        "status" => {
            event.kind = MarketEventKind::InstrumentStatus;
        },
        _ => {
            event.kind = MarketEventKind::Ticker24h;
            event.price = optional(row, "last")?;
            event.quantity = optional(row, "lastSz")?;
            event.ask_price = optional(row, "askPx")?;
            event.ask_quantity = optional(row, "askSz")?;
        },
    }
    Ok(event)
}

fn empty_event(
    symbol: &str,
    observed_at_unix_nanos: UnixNanos,
) -> Result<MarketEvent, IntegrationError> {
    Ok(MarketEvent {
        symbol: ParticipantSymbol::new(symbol).map_err(payload)?,
        kind: MarketEventKind::Snapshot,
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
        venue: MarketVenueEvidence::default(),
    })
}

fn integer(value: Option<&Value>) -> Option<u64> {
    value.and_then(|value| {
        value
            .as_u64()
            .or_else(|| value.as_i64().and_then(|value| u64::try_from(value).ok()))
            .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
    })
}

fn rows(value: &Value) -> Result<&Vec<Value>, IntegrationError> {
    value
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| IntegrationError::InvalidPayload("OKX response data is missing".into()))
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
                IntegrationError::InvalidPayload("OKX order book level must be an array".into())
            })?;
            Ok((
                required_value(level.first())?,
                required_value(level.get(1))?,
            ))
        })
        .collect()
}

fn text<'a>(value: &'a Value, field: &str) -> Option<&'a str> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn optional<T: std::str::FromStr>(value: &Value, field: &str) -> Result<Option<T>, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    optional_value(value.get(field))
}

fn optional_value<T: std::str::FromStr>(
    value: Option<&Value>,
) -> Result<Option<T>, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    value
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::parse)
        .transpose()
        .map_err(payload)
}

fn required<T: std::str::FromStr>(value: &Value, field: &str) -> Result<T, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    required_value(value.get(field))
}

fn required_value<T: std::str::FromStr>(value: Option<&Value>) -> Result<T, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    value
        .and_then(Value::as_str)
        .ok_or_else(|| IntegrationError::InvalidPayload("OKX numeric field is missing".into()))?
        .parse()
        .map_err(payload)
}

fn timestamp(value: Option<&Value>) -> UnixNanos {
    value
        .and_then(|value| {
            value
                .as_str()
                .and_then(|value| value.parse::<u64>().ok())
                .or_else(|| value.as_u64())
        })
        .map(|value| UnixNanos::from(value.saturating_mul(1_000_000)))
        .unwrap_or_else(|| UnixNanos::from(0))
}

fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn feed_mapping_preserves_typed_market_intent() {
        let feed = MarketFeed {
            kind: MarketDataKind::FundingRate,
            symbol: Some(ParticipantSymbol::new("BTC-USDT-SWAP").unwrap()),
            interval: None,
            depth: None,
            update_speed_millis: None,
        };

        assert_eq!(
            feed_argument(&feed).unwrap(),
            json!({"channel":"funding-rate","instId":"BTC-USDT-SWAP"})
        );
    }

    #[test]
    fn stream_book_keeps_delta_sequence_and_both_sides() {
        let events = stream_events(&json!({
            "arg":{"channel":"books","instId":"BTC-USDT"},
            "action":"update",
            "data":[{
                "bids":[["10.0","2.0","0","1"]],
                "asks":[["11.0","3.0","0","1"]],
                "prevSeqId":"41",
                "seqId":"42",
                "ts":"1000"
            }]
        }))
        .unwrap();

        let event = events.front().unwrap();
        assert_eq!(event.kind, MarketEventKind::BookDelta);
        assert_eq!(event.first_sequence.map(|value| value.get()), Some(41));
        assert_eq!(event.last_sequence.map(|value| value.get()), Some(42));
        assert_eq!(event.bids.len(), 1);
        assert_eq!(event.asks.len(), 1);
        assert_eq!(event.observed_at_unix_nanos.get(), 1_000_000_000);
    }

    #[test]
    fn stream_candle_keeps_interval_and_ohlcv() {
        let events = stream_events(&json!({
            "arg":{"channel":"candle1m","instId":"BTC-USDT"},
            "data":[["1000","10","12","9","11","4"]]
        }))
        .unwrap();

        let event = events.front().unwrap();
        assert_eq!(event.kind, MarketEventKind::Bar);
        assert_eq!(event.bar.as_ref().unwrap().timeframe, "1m");
        assert_eq!(event.observed_at_unix_nanos.get(), 1_000_000_000);
    }

    #[test]
    fn rest_derivative_normalizers_preserve_typed_values() {
        let symbol = ParticipantSymbol::new("BTC-USDT-SWAP").unwrap();
        let mark =
            mark_price(&symbol, &json!({"data":[{"markPx":"42000.5","ts":"1000"}]})).unwrap();
        let funding = funding_rate(
            &symbol,
            &json!({"data":[{
                "fundingRate":"0.0001",
                "fundingTime":"1000",
                "nextFundingTime":"2000"
            }]}),
        )
        .unwrap();
        let interest =
            open_interest(&symbol, &json!({"data":[{"oi":"125.5","ts":"1000"}]})).unwrap();

        assert_eq!(mark.symbol, symbol);
        assert_eq!(mark.observed_at_unix_nanos.get(), 1_000_000_000);
        assert_eq!(
            funding.next_funding_at_unix_nanos.unwrap().get(),
            2_000_000_000
        );
        assert_eq!(interest.observed_at_unix_nanos.get(), 1_000_000_000);
    }

    #[test]
    fn option_summary_selects_the_requested_contract() {
        let symbol = ParticipantSymbol::new("BTC-USD-260925-100000-C").unwrap();
        let result = greeks(
            &symbol,
            &json!({"data":[
                {"instId":"BTC-USD-260925-90000-C","delta":"0.8","ts":"1000"},
                {"instId":"BTC-USD-260925-100000-C","delta":"0.5","gamma":"0.1","ts":"2000"}
            ]}),
        )
        .unwrap();

        assert_eq!(result.symbol, symbol);
        assert_eq!(result.observed_at_unix_nanos.get(), 2_000_000_000);
        assert!(result.values.delta.is_some());
    }
}
