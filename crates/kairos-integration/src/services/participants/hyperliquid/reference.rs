//! Hyperliquid public metadata normalization without canonical Reference IDs.

use kairos_domain_types::{Currency, ProviderSymbol};
use serde_json::Value;

use crate::application::capabilities::reference::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
};
use crate::application::capabilities::{ParticipantKind, ParticipantRef};
use crate::application::IntegrationError;

pub(crate) fn normalize(payload: &Value) -> Result<ExternalInstrumentCatalog, IntegrationError> {
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

#[cfg(test)]
mod tests {
    use super::normalize;
    use crate::application::capabilities::reference::ExternalInstrumentKind;

    #[test]
    fn preserves_hyperliquid_metadata_without_canonical_ids() {
        let facts = normalize(&serde_json::json!([
            {"universe": [{"name": "BTC", "szDecimals": 5}]},
            [{"markPx": "50000"}]
        ]))
        .unwrap();
        assert_eq!(facts.instruments[0].kind, ExternalInstrumentKind::Perpetual);
        assert_eq!(facts.instruments[0].quantity_precision, Some(5));
    }
}
