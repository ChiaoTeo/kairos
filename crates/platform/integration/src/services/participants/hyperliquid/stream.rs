use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::time::UnixNanos;
use serde_json::Value;

use crate::{Bar, IntegrationError, MarketEvent, MarketEventKind};

pub(crate) fn book(data: &Value) -> Result<MarketEvent, IntegrationError> {
    let coin = text(data, "coin")?;
    let sides = data
        .get("levels")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid book levels missing".into())
        })?;
    let mut event = empty(
        coin,
        MarketEventKind::BookSnapshot,
        millis(data.get("time").and_then(Value::as_u64)),
    )?;
    event.bids = levels(sides.first())?;
    event.asks = levels(sides.get(1))?;
    Ok(event)
}

pub(crate) fn trade(row: &Value) -> Result<MarketEvent, IntegrationError> {
    let coin = text(row, "coin")?;
    let mut event = empty(
        coin,
        MarketEventKind::Trade,
        millis(row.get("time").and_then(Value::as_u64)),
    )?;
    event.price = optional(row.get("px"))?;
    event.quantity = optional(row.get("sz"))?;
    Ok(event)
}

pub(crate) fn best_bid_offer(data: &Value) -> Result<MarketEvent, IntegrationError> {
    let coin = text(data, "coin")?;
    let sides = data.get("bbo").and_then(Value::as_array).ok_or_else(|| {
        IntegrationError::InvalidPayload("Hyperliquid bbo data is missing".into())
    })?;
    let mut event = empty(
        coin,
        MarketEventKind::Quote,
        millis(data.get("time").and_then(Value::as_u64)),
    )?;
    if let Some(bid) = sides.first().and_then(|value| value.as_object()) {
        event.price = optional(bid.get("px"))?;
        event.quantity = optional(bid.get("sz"))?;
    }
    if let Some(ask) = sides.get(1).and_then(|value| value.as_object()) {
        event.ask_price = optional(ask.get("px"))?;
        event.ask_quantity = optional(ask.get("sz"))?;
    }
    Ok(event)
}

pub(crate) fn active_asset_context(data: &Value) -> Result<Vec<MarketEvent>, IntegrationError> {
    let coin = text(data, "coin")?;
    let context = data.get("ctx").unwrap_or(data);
    let observed = millis(
        data.get("time")
            .and_then(Value::as_u64)
            .or_else(|| context.get("time").and_then(Value::as_u64)),
    );
    let mut events = Vec::new();
    for (field, kind) in [
        ("markPx", MarketEventKind::MarkPrice),
        ("oraclePx", MarketEventKind::IndexPrice),
    ] {
        if let Some(price) = optional(context.get(field))? {
            let mut event = empty(coin, kind, observed)?;
            event.price = Some(price);
            events.push(event);
        }
    }
    if let Some(rate) = optional(context.get("funding"))? {
        let mut event = empty(coin, MarketEventKind::FundingRate, observed)?;
        event.rate = Some(rate);
        events.push(event);
    }
    if let Some(quantity) = optional(context.get("openInterest"))? {
        let mut event = empty(coin, MarketEventKind::OpenInterest, observed)?;
        event.quantity = Some(quantity);
        events.push(event);
    }
    Ok(events)
}

pub(crate) fn candle(row: &Value) -> Result<MarketEvent, IntegrationError> {
    let coin = text(row, "s")?;
    let mut event = empty(
        coin,
        MarketEventKind::Bar,
        millis(row.get("t").and_then(Value::as_u64)),
    )?;
    event.bar = Some(Bar {
        timeframe: row
            .get("i")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
        open: required(row.get("o"))?,
        high: required(row.get("h"))?,
        low: required(row.get("l"))?,
        close: required(row.get("c"))?,
        volume: optional(row.get("v"))?,
        derivation: "participant".into(),
    });
    Ok(event)
}

pub(crate) fn empty(
    symbol: &str,
    kind: MarketEventKind,
    time: UnixNanos,
) -> Result<MarketEvent, IntegrationError> {
    Ok(MarketEvent {
        symbol: ParticipantSymbol::new(symbol).map_err(payload)?,
        kind,
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
        observed_at_unix_nanos: time,
        venue: Default::default(),
    })
}

pub(crate) fn optional<T>(value: Option<&Value>) -> Result<Option<T>, IntegrationError>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    value
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::parse)
        .transpose()
        .map_err(payload)
}

pub(crate) fn millis(value: Option<u64>) -> UnixNanos {
    value
        .map(|value| UnixNanos::from(value.saturating_mul(1_000_000)))
        .unwrap_or_else(now)
}

pub(crate) fn now() -> UnixNanos {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(nanos).unwrap_or(u64::MAX))
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
        .map(|row| Ok((required(row.get("px"))?, required(row.get("sz"))?)))
        .collect()
}

fn required<T>(value: Option<&Value>) -> Result<T, IntegrationError>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    value
        .and_then(Value::as_str)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid numeric value missing".into())
        })?
        .parse()
        .map_err(payload)
}

fn text<'a>(value: &'a Value, field: &str) -> Result<&'a str, IntegrationError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Hyperliquid {field} missing")))
}

fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn book_preserves_both_sides_and_provider_time() {
        let event = book(&json!({
            "coin": "BTC",
            "time": 1000,
            "levels": [
                [{"px": "10", "sz": "2"}],
                [{"px": "11", "sz": "3"}]
            ]
        }))
        .unwrap();
        assert_eq!(event.kind, MarketEventKind::BookSnapshot);
        assert_eq!(event.bids.len(), 1);
        assert_eq!(event.asks.len(), 1);
        assert_eq!(event.observed_at_unix_nanos.get(), 1_000_000_000);
    }

    #[test]
    fn bbo_preserves_both_sides() {
        let event = best_bid_offer(&json!({
            "coin":"BTC","time":1000,
            "bbo":[{"px":"10","sz":"2"},{"px":"11","sz":"3"}]
        }))
        .unwrap();
        assert_eq!(event.kind, MarketEventKind::Quote);
        assert_eq!(event.observed_at_unix_nanos.get(), 1_000_000_000);
        assert!(event.price.is_some());
        assert!(event.ask_price.is_some());
    }

    #[test]
    fn active_context_expands_shared_provider_frame_into_typed_observations() {
        let events = active_asset_context(&json!({
            "coin":"BTC","ctx":{
                "markPx":"10","oraclePx":"9","funding":"0.0001","openInterest":"20"
            }
        }))
        .unwrap();
        assert_eq!(events.len(), 4);
        assert!(
            events
                .iter()
                .any(|event| event.kind == MarketEventKind::MarkPrice)
        );
        assert!(
            events
                .iter()
                .any(|event| event.kind == MarketEventKind::IndexPrice)
        );
        assert!(
            events
                .iter()
                .any(|event| event.kind == MarketEventKind::FundingRate)
        );
        assert!(
            events
                .iter()
                .any(|event| event.kind == MarketEventKind::OpenInterest)
        );
    }
}
