//! Binance Stocks Trading reference normalization.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::application::reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceEntity, ReferenceInstrument,
    ReferenceListing, ReferenceMarket,
};

pub fn catalog(payload: &Value) -> Result<ReferenceCatalogPayload, String> {
    let symbols = payload
        .get("symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance Equity exchangeInfo.symbols is missing".to_string())?;
    let mut assets = BTreeMap::new();
    let mut result = ReferenceCatalogPayload {
        entities: vec![ReferenceEntity {
            entity_id: "binance".into(),
            entity_type: "venue".into(),
            name: "Binance".into(),
            status: "active".into(),
        }],
        ..Default::default()
    };
    for value in symbols {
        let symbol = value
            .get("symbol")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| "Binance Equity symbol is required".to_string())?;
        let status = if value
            .get("tradability")
            .and_then(Value::as_str)
            .map(|value| value.eq_ignore_ascii_case("NONE"))
            .unwrap_or(false)
        {
            "halted"
        } else {
            "active"
        };
        let asset_id = format!("asset:equity:{}", symbol.to_ascii_uppercase());
        assets
            .entry(asset_id.clone())
            .or_insert_with(|| ReferenceAsset {
                asset_id: asset_id.clone(),
                code: symbol.to_ascii_uppercase(),
                asset_class: "equity".into(),
                status: status.into(),
            });
        let key = symbol.to_ascii_uppercase();
        let instrument_id = format!("instrument:binance:equity:{key}");
        let listing_id = format!("listing:binance:equity:{key}");
        result.instruments.push(ReferenceInstrument {
            instrument_id: instrument_id.clone(),
            symbol: key.clone(),
            instrument_type: "equity".into(),
            product_family: Some("equity".into()),
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            status: status.into(),
        });
        result.listings.push(ReferenceListing {
            listing_id: listing_id.clone(),
            instrument_id: instrument_id.clone(),
            venue_id: "binance".into(),
            venue_symbol: key.clone(),
            status: status.into(),
            effective_from_unix_nanos: now_unix_nanos(),
        });
        result.markets.push(ReferenceMarket {
            market_id: format!("market:binance:equity:{key}"),
            market_key: format!("binance.equity.{key}"),
            instrument_id,
            listing_id,
            venue_id: "binance".into(),
            market_type: "equity".into(),
            source_symbol: key,
            base_asset_id: Some(asset_id),
            quote_asset_id: None,
            status: status.into(),
            price_tick: None,
            quantity_tick: string(value, "stepSize"),
            price_precision: 0,
            quantity_precision: 0,
            minimum_quantity: string(value, "minQty"),
            minimum_notional: string(value, "minNotional"),
            contract_size: None,
            effective_to_unix_nanos: None,
        });
    }
    result.assets = assets.into_values().collect();
    Ok(result)
}

fn string(value: &Value, field: &str) -> Option<String> {
    value.get(field).and_then(Value::as_str).map(str::to_owned)
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
    fn normalizes_stocks_exchange_info_into_equity_reference_payload() {
        let payload = serde_json::json!({
            "symbols": [{
                "symbol": "AAPL",
                "tradability": "TRADING",
                "stepSize": "1",
                "minQty": "1"
            }]
        });
        let result = catalog(&payload).unwrap();
        assert_eq!(result.entities[0].entity_id, "binance");
        assert_eq!(result.instruments[0].instrument_type, "equity");
        assert_eq!(result.markets[0].source_symbol, "AAPL");
        assert_eq!(result.markets[0].quantity_tick.as_deref(), Some("1"));
    }

    #[test]
    fn rejects_stocks_exchange_info_without_symbols() {
        assert!(catalog(&serde_json::json!({})).is_err());
    }
}
