//! Hyperliquid public metadata normalization without canonical Reference IDs.

use kairos_primitives::{Currency, ProviderSymbol};
use serde_json::Value;

use crate::application::capabilities::reference::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
};
use crate::application::capabilities::{ParticipantKind, ParticipantRef};
use crate::application::IntegrationError;

pub(crate) fn normalize_perpetual(
    payload: &Value,
) -> Result<ExternalInstrumentCatalog, IntegrationError> {
    let meta = payload
        .as_array()
        .and_then(|items| items.first())
        .ok_or_else(|| {
            IntegrationError::InvalidPayload(
                "Hyperliquid metaAndAssetCtxs response is missing metadata".into(),
            )
        })?;
    let universe = meta
        .get("universe")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid metadata universe is missing".into())
        })?;
    let instruments = universe
        .iter()
        .map(|item| {
            let symbol = item.get("name").and_then(Value::as_str).ok_or_else(|| {
                IntegrationError::InvalidPayload("Hyperliquid universe item name is missing".into())
            })?;
            Ok(ExternalInstrument {
                source_symbol: ProviderSymbol::new(symbol)
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                source_venue: None,
                kind: ExternalInstrumentKind::Perpetual,
                base_currency: Some(
                    Currency::new(symbol)
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                ),
                quote_currency: Some(Currency::new("USDC").expect("static currency")),
                settlement_currency: Some(Currency::new("USDC").expect("static currency")),
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: !item
                    .get("isDelisted")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                price_tick: None,
                quantity_tick: None,
                minimum_quantity: None,
                minimum_notional: None,
                contract_value: None,
                price_precision: None,
                quantity_precision: item
                    .get("szDecimals")
                    .and_then(Value::as_u64)
                    .and_then(|value| u32::try_from(value).ok()),
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid")
            .expect("static Hyperliquid participant"),
        instruments,
    })
}

pub(crate) fn normalize_spot(
    payload: &Value,
) -> Result<ExternalInstrumentCatalog, IntegrationError> {
    let meta = payload
        .as_array()
        .and_then(|items| items.first())
        .ok_or_else(|| {
            IntegrationError::InvalidPayload(
                "Hyperliquid spotMetaAndAssetCtxs response is missing metadata".into(),
            )
        })?;
    let tokens = meta
        .get("tokens")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid spot tokens are missing".into())
        })?;
    let universe = meta
        .get("universe")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid spot universe is missing".into())
        })?;

    let token = |index: usize| -> Result<&Value, IntegrationError> {
        tokens.get(index).ok_or_else(|| {
            IntegrationError::InvalidPayload(format!(
                "Hyperliquid spot token index {index} is out of bounds"
            ))
        })
    };
    let token_name = |value: &Value| -> Result<Currency, IntegrationError> {
        let name = value.get("name").and_then(Value::as_str).ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid spot token name is missing".into())
        })?;
        Currency::new(name).map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
    };

    let instruments = universe
        .iter()
        .map(|item| {
            let symbol = item.get("name").and_then(Value::as_str).ok_or_else(|| {
                IntegrationError::InvalidPayload("Hyperliquid spot universe name is missing".into())
            })?;
            let pair = item
                .get("tokens")
                .and_then(Value::as_array)
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload(
                        "Hyperliquid spot token pair is missing".into(),
                    )
                })?;
            if pair.len() != 2 {
                return Err(IntegrationError::InvalidPayload(
                    "Hyperliquid spot token pair must contain base and quote indexes".into(),
                ));
            }
            let base_index = pair[0]
                .as_u64()
                .and_then(|value| usize::try_from(value).ok())
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload(
                        "Hyperliquid spot base token index is invalid".into(),
                    )
                })?;
            let quote_index = pair[1]
                .as_u64()
                .and_then(|value| usize::try_from(value).ok())
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload(
                        "Hyperliquid spot quote token index is invalid".into(),
                    )
                })?;
            let base_token = token(base_index)?;
            let base_currency = token_name(base_token)?;
            let quote_currency = token_name(token(quote_index)?)?;
            Ok(ExternalInstrument {
                source_symbol: ProviderSymbol::new(symbol)
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                source_venue: None,
                kind: ExternalInstrumentKind::Spot,
                base_currency: Some(base_currency),
                quote_currency: Some(quote_currency),
                settlement_currency: None,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: !item
                    .get("isDelisted")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                price_tick: None,
                quantity_tick: None,
                minimum_quantity: None,
                minimum_notional: None,
                contract_value: None,
                price_precision: None,
                quantity_precision: base_token
                    .get("szDecimals")
                    .and_then(Value::as_u64)
                    .and_then(|value| u32::try_from(value).ok()),
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid")
            .expect("static Hyperliquid participant"),
        instruments,
    })
}

#[cfg(test)]
mod tests {
    use super::{normalize_perpetual, normalize_spot};
    use crate::application::capabilities::reference::ExternalInstrumentKind;

    #[test]
    fn preserves_hyperliquid_metadata_without_canonical_ids() {
        let facts = normalize_perpetual(&serde_json::json!([
            {"universe": [{"name": "BTC", "szDecimals": 5}]},
            [{"markPx": "50000"}]
        ]))
        .unwrap();
        assert_eq!(facts.instruments[0].kind, ExternalInstrumentKind::Perpetual);
        assert_eq!(facts.instruments[0].quantity_precision, Some(5));
    }

    #[test]
    fn normalizes_spot_tokens_and_provider_symbol() {
        let facts = normalize_spot(&serde_json::json!([
            {
                "tokens": [
                    {"name": "USDC", "szDecimals": 8, "index": 0},
                    {"name": "PURR", "szDecimals": 0, "index": 1}
                ],
                "universe": [{"name": "PURR/USDC", "tokens": [1, 0], "index": 0}]
            },
            [{"midPx": "0.1"}]
        ]))
        .unwrap();
        assert_eq!(facts.instruments[0].kind, ExternalInstrumentKind::Spot);
        assert_eq!(facts.instruments[0].source_symbol.as_str(), "PURR/USDC");
        assert_eq!(
            facts.instruments[0]
                .base_currency
                .as_ref()
                .unwrap()
                .as_str(),
            "PURR"
        );
        assert_eq!(
            facts.instruments[0]
                .quote_currency
                .as_ref()
                .unwrap()
                .as_str(),
            "USDC"
        );
        assert_eq!(facts.instruments[0].quantity_precision, Some(0));
    }
}
