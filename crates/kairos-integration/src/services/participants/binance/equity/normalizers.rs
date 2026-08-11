//! Binance Stocks Trading instrument normalization without canonical Reference IDs.

use kairos_domain_types::{Currency, ProviderSymbol};
use serde_json::Value;

use crate::application::capabilities::reference::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
};
use crate::application::capabilities::{ParticipantKind, ParticipantRef};
use crate::application::IntegrationError;

pub(crate) fn catalog(payload: &Value) -> Result<ExternalInstrumentCatalog, IntegrationError> {
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
            let source_symbol = row
                .get("symbol")
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload("Binance Equity symbol is required".into())
                })?;
            let active = !row
                .get("tradability")
                .and_then(Value::as_str)
                .is_some_and(|value| value.eq_ignore_ascii_case("NONE"));
            Ok(ExternalInstrument {
                source_symbol: ProviderSymbol::new(source_symbol)
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                source_venue: text(row, "primaryExchange")
                    .or_else(|| text(row, "exchange"))
                    .map(str::to_owned),
                kind: ExternalInstrumentKind::Equity,
                base_currency: None,
                quote_currency: Some(
                    Currency::new(text(row, "quoteAsset").unwrap_or("USD"))
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                ),
                settlement_currency: text(row, "settlementAsset")
                    .or(Some("USDC"))
                    .map(Currency::new)
                    .transpose()
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active,
                price_tick: value_text(row, "tickSize"),
                quantity_tick: value_text(row, "stepSize"),
                minimum_quantity: value_text(row, "minQty"),
                minimum_notional: value_text(row, "minNotional"),
                contract_value: None,
                price_precision: unsigned(row, "pricePrecision"),
                quantity_precision: unsigned(row, "quantityPrecision"),
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Exchange, "binance")
            .expect("static Binance participant"),
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

fn unsigned(row: &Value, field: &str) -> Option<u32> {
    row.get(field)
        .and_then(|value| value.as_u64().or_else(|| value.as_str()?.parse().ok()))
        .and_then(|value| u32::try_from(value).ok())
}

#[cfg(test)]
mod tests {
    use super::catalog;
    use crate::application::capabilities::reference::ExternalInstrumentKind;

    #[test]
    fn preserves_equity_provider_facts_without_canonical_ids() {
        let result = catalog(&serde_json::json!({
            "symbols": [{
                "symbol": "AAPL",
                "tradability": "TRADING",
                "primaryExchange": "XNAS",
                "stepSize": "1",
                "minQty": "1"
            }]
        }))
        .unwrap();

        assert_eq!(result.participant.id, "binance");
        assert_eq!(result.instruments[0].source_symbol, "AAPL");
        assert_eq!(result.instruments[0].source_venue.as_deref(), Some("XNAS"));
        assert_eq!(result.instruments[0].kind, ExternalInstrumentKind::Equity);
        assert_eq!(result.instruments[0].quantity_tick.as_deref(), Some("1"));
    }

    #[test]
    fn rejects_stocks_exchange_info_without_symbols() {
        assert!(catalog(&serde_json::json!({})).is_err());
    }
}
