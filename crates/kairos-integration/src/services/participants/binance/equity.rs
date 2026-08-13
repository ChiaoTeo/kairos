//! Binance Stocks Trading catalog normalization.
//!
//! The catalog and single-symbol quote are separate read-only capabilities;
//! this module does not imply order-entry or order-query support.

use kairos_domain_types::{Price, ProviderSymbol, Quantity, Symbol, UnixNanos};
use serde_json::Value;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::capabilities::reference::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
};
use crate::application::capabilities::{ParticipantKind, ParticipantRef};
use crate::application::IntegrationError;
use crate::application::MarketQuote;

pub(crate) fn normalize_quote(
    payload: &Value,
    requested_symbol: &ProviderSymbol,
) -> Result<Option<MarketQuote>, IntegrationError> {
    if payload.is_null() || payload.as_object().is_some_and(|value| value.is_empty()) {
        return Ok(None);
    }
    let symbol = text(payload, "symbol")
        .unwrap_or(requested_symbol.as_str())
        .to_ascii_uppercase();
    let bid_price = decimal::<Price>(payload, &["bidPrice", "bid_price"])?;
    let bid_quantity = decimal::<Quantity>(payload, &["bidQty", "bidQuantity", "bid_quantity"])?;
    let ask_price = decimal::<Price>(payload, &["askPrice", "ask_price"])?;
    let ask_quantity = decimal::<Quantity>(payload, &["askQty", "askQuantity", "ask_quantity"])?;
    if bid_price.is_none()
        && bid_quantity.is_none()
        && ask_price.is_none()
        && ask_quantity.is_none()
    {
        return Ok(None);
    }
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .try_into()
        .unwrap_or(u64::MAX);
    Ok(Some(MarketQuote {
        symbol: Symbol::new(symbol)
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        bid_price,
        bid_quantity,
        ask_price,
        ask_quantity,
        last_price: None,
        observed_at_unix_nanos: UnixNanos::new(nanos),
    }))
}

fn decimal<T>(value: &Value, fields: &[&str]) -> Result<Option<T>, IntegrationError>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    let Some(raw) = fields.iter().find_map(|field| text(value, field)) else {
        return Ok(None);
    };
    raw.parse::<T>().map(Some).map_err(|error| {
        IntegrationError::InvalidPayload(format!("invalid equity quote decimal {raw}: {error}"))
    })
}

pub(crate) fn normalize_catalog(
    payload: &Value,
) -> Result<ExternalInstrumentCatalog, IntegrationError> {
    let rows = payload
        .get("symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload(
                "Binance Equity exchangeInfo.symbols is missing".into(),
            )
        })?;
    let instruments = rows
        .iter()
        .map(|row| {
            let source_symbol = text(row, "symbol")
                .filter(|value| !value.trim().is_empty())
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload("Binance Equity symbol is required".into())
                })?;
            let tradability = text(row, "tradability").ok_or_else(|| {
                IntegrationError::InvalidPayload(format!(
                    "Binance Equity {source_symbol} tradability is missing"
                ))
            })?;
            let active = match tradability {
                "BUY_SELL" | "BUY_ONLY" | "SELL_ONLY" => true,
                "NONE" | "OFFMARKET" => false,
                other => {
                    return Err(IntegrationError::InvalidPayload(format!(
                        "unsupported Binance Equity tradability {other} for {source_symbol}"
                    )))
                }
            };
            Ok(ExternalInstrument {
                source_symbol: ProviderSymbol::new(source_symbol)
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                source_venue: None,
                kind: ExternalInstrumentKind::Equity,
                base_currency: None,
                quote_currency: None,
                settlement_currency: None,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active,
                price_tick: None,
                quantity_tick: value_text(row, "stepSize"),
                minimum_quantity: value_text(row, "minQty"),
                minimum_notional: value_text(row, "minNotional"),
                contract_value: None,
                price_precision: None,
                quantity_precision: decimal_scale(row.get("stepSize")),
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Broker, "binance")
            .expect("static Binance broker participant"),
        instruments,
    })
}

fn text<'a>(row: &'a Value, field: &str) -> Option<&'a str> {
    row.get(field).and_then(Value::as_str)
}

fn value_text(row: &Value, field: &str) -> Option<String> {
    row.get(field).and_then(|value| match value {
        Value::String(value) => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    })
}

fn decimal_scale(value: Option<&Value>) -> Option<u32> {
    let value = value?.as_str()?;
    let fractional = value.split_once('.').map(|(_, value)| value).unwrap_or("");
    Some(fractional.trim_end_matches('0').len() as u32)
}

#[cfg(test)]
mod tests {
    use super::{normalize_catalog, normalize_quote};
    use crate::application::capabilities::reference::ExternalInstrumentKind;
    use kairos_domain_types::ProviderSymbol;

    #[test]
    fn normalizes_real_binance_equity_exchange_info_shape() {
        let catalog = normalize_catalog(&serde_json::json!({
            "timezone": "UTC",
            "symbols": [{
                "symbol": "AAPL",
                "tradability": "BUY_SELL",
                "overnightSupported": true,
                "fractionable": true,
                "extendedSession": true,
                "stepSize": "0.000000001",
                "minNotional": "5.00000000",
                "listingTime": 1778233200000_u64
            }]
        }))
        .unwrap();
        let instrument = &catalog.instruments[0];
        assert_eq!(catalog.participant.id, "binance");
        assert_eq!(instrument.source_symbol, "AAPL");
        assert_eq!(instrument.kind, ExternalInstrumentKind::Equity);
        assert_eq!(instrument.quantity_tick.as_deref(), Some("0.000000001"));
        assert_eq!(instrument.minimum_notional.as_deref(), Some("5.00000000"));
        assert!(instrument.active);
    }

    #[test]
    fn rejects_unknown_tradability_instead_of_guessing() {
        assert!(normalize_catalog(&serde_json::json!({
            "symbols": [{"symbol": "AAPL", "tradability": "MYSTERY"}]
        }))
        .is_err());
    }

    #[test]
    fn normalizes_best_bid_and_ask_quote() {
        let quote = normalize_quote(
            &serde_json::json!({
                "symbol": "AAPL",
                "bidPrice": "201.25",
                "bidQty": "3.5",
                "askPrice": "201.30",
                "askQty": "4"
            }),
            &ProviderSymbol::new("AAPL").unwrap(),
        )
        .unwrap()
        .unwrap();
        assert_eq!(quote.symbol.as_str(), "AAPL");
        assert_eq!(quote.bid_price.unwrap().to_string(), "201.25");
        assert_eq!(quote.bid_quantity.unwrap().to_string(), "3.5");
        assert_eq!(quote.ask_price.unwrap().to_string(), "201.3");
        assert_eq!(quote.ask_quantity.unwrap().to_string(), "4");
    }

    #[test]
    fn empty_quote_response_is_absent_not_null() {
        let symbol = ProviderSymbol::new("UNKNOWN").unwrap();
        assert_eq!(
            normalize_quote(&serde_json::Value::Null, &symbol).unwrap(),
            None
        );
        assert_eq!(
            normalize_quote(&serde_json::json!({}), &symbol).unwrap(),
            None
        );
    }
}
