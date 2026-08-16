//! Binance Spot public market payload normalization.

use std::time::{SystemTime, UNIX_EPOCH};

use kairos_domain_types::{Price, Quantity, Sequence, Symbol};
use serde_json::Value;

use crate::application::capabilities::{MarketBar, MarketEvent, MarketEventKind};

pub(crate) fn normalize_market_message(payload: &str) -> Result<Option<MarketEvent>, String> {
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

pub(crate) fn normalize_depth_snapshot(
    symbol: &str,
    value: &Value,
    sequence: u64,
) -> Result<MarketEvent, String> {
    snapshot_event(symbol, value, sequence)
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
    use super::normalize_market_message;
    use crate::application::MarketEventKind;

    #[test]
    fn normalizes_trade_quote_depth_and_bar_messages() {
        let fixtures = [
            (
                r#"{"e":"trade","E":1,"s":"BTCUSDT","t":42,"p":"100.1","q":"0.2"}"#,
                MarketEventKind::Trade,
            ),
            (
                r#"{"u":9,"s":"BTCUSDT","b":"100","B":"2","a":"101","A":"3"}"#,
                MarketEventKind::Quote,
            ),
            (
                r#"{"e":"depthUpdate","E":1,"s":"BTCUSDT","U":10,"u":11,"b":[["100","2"]],"a":[["101","3"]]}"#,
                MarketEventKind::BookDelta,
            ),
            (
                r#"{"e":"kline","E":1,"s":"BTCUSDT","k":{"i":"1m","o":"100","h":"102","l":"99","c":"101","v":"12.5","L":77}}"#,
                MarketEventKind::Bar,
            ),
        ];
        for (payload, kind) in fixtures {
            let event = normalize_market_message(payload).unwrap().unwrap();
            assert_eq!(event.kind, kind);
            assert_eq!(event.symbol.as_str(), "BTCUSDT");
        }
    }
}
