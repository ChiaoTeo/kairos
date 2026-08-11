//! Binance instrument payload normalization without canonical Reference IDs.

use kairos_domain_types::{Currency, ProviderSymbol, UnixNanos};
use serde_json::Value;

use crate::application::capabilities::reference::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
};
use crate::application::capabilities::{ParticipantKind, ParticipantRef};
use crate::application::IntegrationError;

pub(crate) fn normalize_spot(
    payload: &Value,
) -> Result<ExternalInstrumentCatalog, IntegrationError> {
    let rows = payload
        .get("symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance exchangeInfo.symbols is missing".into())
        })?;
    let instruments = rows
        .iter()
        .map(|row| {
            let (price_tick, quantity_tick, minimum_quantity, minimum_notional) =
                spot_filters(row.get("filters"));
            Ok(ExternalInstrument {
                source_symbol: provider_symbol(required(row, "symbol")?)?,
                source_venue: None,
                kind: ExternalInstrumentKind::Spot,
                base_currency: Some(currency(required(row, "baseAsset")?)?),
                quote_currency: Some(currency(required(row, "quoteAsset")?)?),
                settlement_currency: None,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: text(row, "status") == Some("TRADING"),
                price_tick,
                quantity_tick,
                minimum_quantity,
                minimum_notional,
                contract_value: None,
                price_precision: unsigned(row, "quoteAssetPrecision"),
                quantity_precision: unsigned(row, "baseAssetPrecision"),
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(catalog(instruments))
}

pub(crate) fn normalize_options(
    payload: &Value,
) -> Result<ExternalInstrumentCatalog, IntegrationError> {
    let rows = payload
        .get("optionSymbols")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload(
                "Binance options exchangeInfo.optionSymbols is missing".into(),
            )
        })?;
    let mut instruments = Vec::with_capacity(rows.len());
    for row in rows {
        let Some(source_symbol) = text(row, "symbol").filter(|value| !value.trim().is_empty())
        else {
            continue;
        };
        let Some(underlying) = text(row, "underlying").filter(|value| !value.trim().is_empty())
        else {
            continue;
        };
        let Some(expiry_millis) = unsigned_64(row, "expiryDate").filter(|value| *value != 0) else {
            continue;
        };
        let Some(strike) = value_text(row, "strikePrice").filter(|value| !value.trim().is_empty())
        else {
            continue;
        };
        let Some(right) = text(row, "side")
            .or_else(|| text(row, "optionType"))
            .and_then(normalize_right)
        else {
            continue;
        };
        let quote = text(row, "quoteAsset").unwrap_or("USDT");
        let base = underlying.strip_suffix(quote).unwrap_or(underlying);
        if base.is_empty() {
            continue;
        }
        let expiry = expiry_millis.checked_mul(1_000_000).ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance option expiry overflows nanoseconds".into())
        })?;
        instruments.push(ExternalInstrument {
            source_symbol: provider_symbol(source_symbol)?,
            source_venue: None,
            kind: ExternalInstrumentKind::Option,
            base_currency: Some(currency(base)?),
            quote_currency: Some(currency(quote)?),
            settlement_currency: Some(currency(quote)?),
            underlying: Some(provider_symbol(underlying)?),
            expiry_unix_nanos: Some(UnixNanos::new(expiry)),
            strike: Some(strike),
            option_right: Some(right.into()),
            active: text(row, "status") == Some("TRADING"),
            price_tick: value_text(row, "tickSize"),
            quantity_tick: value_text(row, "stepSize"),
            minimum_quantity: value_text(row, "minQty"),
            minimum_notional: None,
            contract_value: value_text(row, "unit"),
            price_precision: unsigned(row, "priceScale"),
            quantity_precision: unsigned(row, "quantityScale"),
        });
    }
    if !rows.is_empty() && instruments.is_empty() {
        return Err(IntegrationError::InvalidPayload(
            "Binance options response contained no valid option symbols".into(),
        ));
    }
    Ok(catalog(instruments))
}

pub(crate) fn normalize_derivatives(
    payload: &Value,
) -> Result<ExternalInstrumentCatalog, IntegrationError> {
    let rows = payload
        .get("symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload(
                "Binance derivatives exchangeInfo.symbols is missing".into(),
            )
        })?;
    let instruments = rows
        .iter()
        .map(|row| {
            let contract_type = text(row, "contractType").unwrap_or("PERPETUAL");
            let kind = if contract_type == "PERPETUAL" {
                ExternalInstrumentKind::Perpetual
            } else {
                ExternalInstrumentKind::Future
            };
            let expiry_unix_nanos = if kind == ExternalInstrumentKind::Future {
                let expiry_millis = unsigned_64(row, "deliveryDate").ok_or_else(|| {
                    IntegrationError::InvalidPayload(
                        "Binance dated future deliveryDate is missing".into(),
                    )
                })?;
                Some(UnixNanos::new(
                    expiry_millis.checked_mul(1_000_000).ok_or_else(|| {
                        IntegrationError::InvalidPayload(
                            "Binance derivative expiry overflows nanoseconds".into(),
                        )
                    })?,
                ))
            } else {
                None
            };
            let (price_tick, quantity_tick, minimum_quantity, minimum_notional) =
                spot_filters(row.get("filters"));
            Ok(ExternalInstrument {
                source_symbol: provider_symbol(required(row, "symbol")?)?,
                source_venue: None,
                kind,
                base_currency: Some(currency(required(row, "baseAsset")?)?),
                quote_currency: Some(currency(required(row, "quoteAsset")?)?),
                settlement_currency: text(row, "marginAsset").map(currency).transpose()?,
                underlying: text(row, "pair").map(provider_symbol).transpose()?,
                expiry_unix_nanos,
                strike: None,
                option_right: None,
                active: text(row, "status") == Some("TRADING"),
                price_tick,
                quantity_tick,
                minimum_quantity,
                minimum_notional,
                contract_value: value_text(row, "contractSize"),
                price_precision: unsigned(row, "pricePrecision"),
                quantity_precision: unsigned(row, "quantityPrecision"),
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(catalog(instruments))
}

fn catalog(instruments: Vec<ExternalInstrument>) -> ExternalInstrumentCatalog {
    ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Exchange, "binance")
            .expect("static Binance participant"),
        instruments,
    }
}

fn required<'a>(row: &'a Value, field: &str) -> Result<&'a str, IntegrationError> {
    text(row, field).ok_or_else(|| {
        IntegrationError::InvalidPayload(format!("Binance symbol field {field} is missing"))
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

fn unsigned_64(row: &Value, field: &str) -> Option<u64> {
    row.get(field)
        .and_then(|value| value.as_u64().or_else(|| value.as_str()?.parse().ok()))
}

fn provider_symbol(value: &str) -> Result<ProviderSymbol, IntegrationError> {
    ProviderSymbol::new(value).map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

fn currency(value: &str) -> Result<Currency, IntegrationError> {
    Currency::new(value).map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

fn normalize_right(value: &str) -> Option<&'static str> {
    match value.to_ascii_lowercase().as_str() {
        "c" | "call" => Some("call"),
        "p" | "put" => Some("put"),
        _ => None,
    }
}

fn spot_filters(
    filters: Option<&Value>,
) -> (
    Option<String>,
    Option<String>,
    Option<String>,
    Option<String>,
) {
    let mut price_tick = None;
    let mut quantity_tick = None;
    let mut minimum_quantity = None;
    let mut minimum_notional = None;
    for filter in filters.and_then(Value::as_array).into_iter().flatten() {
        match text(filter, "filterType") {
            Some("PRICE_FILTER") => price_tick = value_text(filter, "tickSize"),
            Some("LOT_SIZE") => {
                quantity_tick = value_text(filter, "stepSize");
                minimum_quantity = value_text(filter, "minQty");
            }
            Some("MIN_NOTIONAL") | Some("NOTIONAL") => {
                minimum_notional = value_text(filter, "minNotional")
            }
            _ => {}
        }
    }
    (
        price_tick,
        quantity_tick,
        minimum_quantity,
        minimum_notional,
    )
}

#[cfg(test)]
mod tests {
    use super::{normalize_derivatives, normalize_options, normalize_spot};
    use crate::application::capabilities::reference::ExternalInstrumentKind;

    #[test]
    fn derivatives_catalog_preserves_contract_facts_without_canonical_ids() {
        let facts = normalize_derivatives(&serde_json::json!({
            "symbols": [{
                "symbol": "BTCUSDT_260626",
                "pair": "BTCUSDT",
                "contractType": "CURRENT_QUARTER",
                "deliveryDate": 1782432000000_u64,
                "status": "TRADING",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
                "pricePrecision": 1,
                "quantityPrecision": 3,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}
                ]
            }]
        }))
        .expect("derivatives facts");
        let instrument = &facts.instruments[0];
        assert_eq!(instrument.kind, ExternalInstrumentKind::Future);
        assert_eq!(instrument.source_symbol.as_str(), "BTCUSDT_260626");
        assert_eq!(instrument.price_tick.as_deref(), Some("0.1"));
        assert_eq!(instrument.quantity_precision, Some(3));
    }

    #[test]
    fn spot_catalog_contains_provider_rules_without_canonical_ids() {
        let facts = normalize_spot(&serde_json::json!({
            "symbols": [{
                "symbol": "BTCUSDT",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "status": "TRADING",
                "baseAssetPrecision": 6,
                "quoteAssetPrecision": 2,
                "filters": [
                    {"filterType":"PRICE_FILTER","tickSize":"0.01"},
                    {"filterType":"LOT_SIZE","stepSize":"0.000001","minQty":"0.00001"},
                    {"filterType":"MIN_NOTIONAL","minNotional":"10"}
                ]
            }]
        }))
        .unwrap();
        assert_eq!(facts.instruments[0].kind, ExternalInstrumentKind::Spot);
        assert_eq!(facts.instruments[0].minimum_notional.as_deref(), Some("10"));
        assert_eq!(facts.instruments[0].price_precision, Some(2));
    }

    #[test]
    fn options_catalog_preserves_contract_metadata() {
        let facts = normalize_options(&serde_json::json!({
            "optionSymbols": [{
                "symbol":"BTC-260821-50000-C",
                "underlying":"BTCUSDT",
                "status":"TRADING",
                "expiryDate":1780000000000u64,
                "strikePrice":"50000",
                "optionType":"CALL",
                "unit":"1"
            }]
        }))
        .unwrap();
        assert_eq!(facts.instruments[0].kind, ExternalInstrumentKind::Option);
        assert_eq!(facts.instruments[0].base_currency.as_deref(), Some("BTC"));
        assert_eq!(facts.instruments[0].option_right.as_deref(), Some("call"));
        assert_eq!(facts.instruments[0].contract_value.as_deref(), Some("1"));
    }
}
