//! Binance Options exchange-info normalization.

use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::application::reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceEntity, ReferenceInstrument,
    ReferenceListing, ReferenceMarket,
};

pub fn catalog(payload: &Value) -> Result<ReferenceCatalogPayload, String> {
    let symbols = payload
        .get("optionSymbols")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance options exchangeInfo.optionSymbols is missing".to_string())?;
    let mut result = ReferenceCatalogPayload {
        entities: vec![ReferenceEntity {
            entity_id: "binance".into(),
            entity_type: "venue".into(),
            name: "Binance".into(),
            status: "active".into(),
        }],
        ..Default::default()
    };
    for symbol in symbols {
        let source_symbol = required(symbol, "symbol")?;
        let underlying = required(symbol, "underlying")?;
        let status = match symbol
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("UNKNOWN")
        {
            "TRADING" => "active".to_string(),
            value => value.to_ascii_lowercase(),
        };
        let underlying_id = format!("instrument:binance:spot:{underlying}");
        let instrument_id = format!("instrument:binance:options:{source_symbol}");
        let listing_id = format!("listing:binance:options:{source_symbol}");
        let market_id = format!("market:binance:options:{source_symbol}");
        let base = underlying.trim_end_matches("USDT").to_string();
        result.assets.push(ReferenceAsset {
            asset_id: format!("asset:crypto:{base}"),
            code: base,
            asset_class: "crypto".into(),
            status: "active".into(),
        });
        result.instruments.push(ReferenceInstrument {
            instrument_id: instrument_id.clone(),
            symbol: source_symbol.clone(),
            instrument_type: "option".into(),
            product_family: Some("options".into()),
            underlying_instrument_id: Some(underlying_id),
            expiry_unix_nanos: timestamp_millis(symbol.get("expiryDate")),
            strike: string(symbol, "strikePrice"),
            option_right: string(symbol, "optionType").map(|value| value.to_ascii_lowercase()),
            status: status.clone(),
        });
        result.listings.push(ReferenceListing {
            listing_id: listing_id.clone(),
            instrument_id: instrument_id.clone(),
            venue_id: "binance".into(),
            venue_symbol: source_symbol.clone(),
            status: status.clone(),
            effective_from_unix_nanos: now_unix_nanos(),
        });
        result.markets.push(ReferenceMarket {
            market_id,
            market_key: format!("binance.options.{source_symbol}"),
            instrument_id,
            listing_id,
            venue_id: "binance".into(),
            market_type: "options".into(),
            asset_type: Some("crypto".into()),
            source_symbol,
            base_asset_id: None,
            quote_asset_id: Some("asset:crypto:USDT".into()),
            status,
            price_tick: None,
            quantity_tick: None,
            price_precision: 0,
            quantity_precision: 0,
            minimum_quantity: None,
            minimum_notional: None,
            contract_size: string(symbol, "unit"),
            effective_to_unix_nanos: timestamp_millis(symbol.get("expiryDate")),
        });
    }
    result
        .assets
        .sort_by(|left, right| left.asset_id.cmp(&right.asset_id));
    result
        .assets
        .dedup_by(|left, right| left.asset_id == right.asset_id);
    Ok(result)
}

fn required(value: &Value, field: &str) -> Result<String, String> {
    string(value, field).ok_or_else(|| format!("Binance option field {field} is missing"))
}

fn string(value: &Value, field: &str) -> Option<String> {
    value.get(field).and_then(|value| match value {
        Value::String(value) => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    })
}

fn timestamp_millis(value: Option<&Value>) -> Option<u64> {
    value.and_then(|value| value.as_u64().or_else(|| value.as_str()?.parse().ok()))
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::catalog;

    #[test]
    fn normalizes_option_contract_metadata_into_neutral_reference_payload() {
        let payload = serde_json::json!({
            "optionSymbols": [{
                "symbol": "BTC-260821-50000-C",
                "underlying": "BTCUSDT",
                "status": "TRADING",
                "expiryDate": 1780000000000u64,
                "strikePrice": "50000",
                "optionType": "CALL",
                "unit": "1"
            }]
        });
        let result = catalog(&payload).unwrap();
        assert_eq!(result.instruments.len(), 1);
        assert_eq!(
            result.instruments[0].underlying_instrument_id.as_deref(),
            Some("instrument:binance:spot:BTCUSDT")
        );
        assert_eq!(result.instruments[0].option_right.as_deref(), Some("call"));
        assert_eq!(result.instruments[0].strike.as_deref(), Some("50000"));
        assert_eq!(result.markets[0].contract_size.as_deref(), Some("1"));
    }
}
