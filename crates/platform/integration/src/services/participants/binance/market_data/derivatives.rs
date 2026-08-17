//! Binance Options public WebSocket payload normalization.

use crate::application::{IntegrationError, MarketEvent, MarketEventKind};
use kairos_primitives::{Price, Quantity, Sequence, Symbol};
use serde_json::Value;

pub(crate) fn normalize_derivatives_market_message(
    payload: &str,
) -> Result<Option<MarketEvent>, String> {
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
        venue: Default::default(),
    }))
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
    use super::normalize_derivatives_market_message;
    use crate::application::MarketEventKind;

    #[test]
    fn normalizes_options_book_ticker() {
        let event = normalize_derivatives_market_message(
            r#"{"e":"bookTicker","E":1,"s":"BTC-260925-145000-C","b":"10","B":"2","a":"11","A":"3","u":9}"#,
        )
        .unwrap()
        .unwrap();
        assert_eq!(event.kind, MarketEventKind::Quote);
        assert_eq!(event.symbol.as_str(), "BTC-260925-145000-C");
        assert_eq!(event.sequence.map(|value| value.get()), Some(9));
    }
}
