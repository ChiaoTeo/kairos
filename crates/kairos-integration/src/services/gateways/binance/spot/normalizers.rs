//! Spot vendor payload normalization boundary.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::application::reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceEntity, ReferenceInstrument,
    ReferenceListing, ReferenceMarket,
};

/// Normalize Binance exchangeInfo before it crosses the integration boundary.
pub fn catalog(payload: &Value) -> Result<ReferenceCatalogPayload, String> {
    let symbols = payload
        .get("symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance exchangeInfo.symbols is missing".to_string())?;
    let mut result = ReferenceCatalogPayload {
        entities: vec![ReferenceEntity {
            entity_id: "binance".into(),
            entity_type: "venue".into(),
            name: "Binance".into(),
            status: "active".into(),
        }],
        ..Default::default()
    };
    let mut assets = BTreeMap::new();
    for symbol in symbols {
        let source_symbol = required(symbol, "symbol")?;
        let base = required(symbol, "baseAsset")?;
        let quote = required(symbol, "quoteAsset")?;
        let status = match symbol
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("UNKNOWN")
        {
            "TRADING" => "active".to_string(),
            value => value.to_ascii_lowercase(),
        };
        assets
            .entry(base.clone())
            .or_insert_with(|| ReferenceAsset {
                asset_id: format!("asset:crypto:{base}"),
                code: base.clone(),
                asset_class: "crypto".into(),
                status: "active".into(),
            });
        assets
            .entry(quote.clone())
            .or_insert_with(|| ReferenceAsset {
                asset_id: format!("asset:crypto:{quote}"),
                code: quote.clone(),
                asset_class: "crypto".into(),
                status: "active".into(),
            });
        let instrument_id = format!("instrument:binance:spot:{base}:{quote}");
        let listing_id = format!("listing:binance:spot:{source_symbol}");
        let market_id = format!("market:binance:spot:{source_symbol}");
        let (price_tick, quantity_tick, minimum_quantity, minimum_notional) =
            filters(symbol.get("filters"));
        result.instruments.push(ReferenceInstrument {
            instrument_id: instrument_id.clone(),
            symbol: format!("{base}/{quote}"),
            instrument_type: "spot".into(),
            product_family: Some("spot".into()),
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
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
            market_key: format!("binance.spot.{source_symbol}"),
            instrument_id,
            listing_id,
            venue_id: "binance".into(),
            market_type: "spot".into(),
            asset_type: Some("crypto".into()),
            source_symbol,
            base_asset_id: Some(format!("asset:crypto:{base}")),
            quote_asset_id: Some(format!("asset:crypto:{quote}")),
            status,
            price_tick,
            quantity_tick,
            price_precision: symbol
                .get("quoteAssetPrecision")
                .and_then(Value::as_i64)
                .unwrap_or_default() as i32,
            quantity_precision: symbol
                .get("baseAssetPrecision")
                .and_then(Value::as_i64)
                .unwrap_or_default() as i32,
            minimum_quantity,
            minimum_notional,
            contract_size: None,
            effective_to_unix_nanos: None,
        });
    }
    result.assets = assets.into_values().collect();
    Ok(result)
}

fn required(value: &Value, field: &str) -> Result<String, String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .ok_or_else(|| format!("Binance symbol field {field} is missing"))
}

fn filters(
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
        match filter.get("filterType").and_then(Value::as_str) {
            Some("PRICE_FILTER") => price_tick = string(filter, "tickSize"),
            Some("LOT_SIZE") => {
                quantity_tick = string(filter, "stepSize");
                minimum_quantity = string(filter, "minQty");
            }
            Some("MIN_NOTIONAL") | Some("NOTIONAL") => {
                minimum_notional = string(filter, "minNotional")
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

fn string(value: &Value, field: &str) -> Option<String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
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
    fn exchange_info_is_normalized_without_vendor_payloads() {
        let payload = serde_json::json!({
            "symbols": [{
                "symbol": "BTCUSDT",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "status": "TRADING",
                "baseAssetPrecision": 6,
                "quoteAssetPrecision": 2,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.000001", "minQty": "0.00001"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "10"}
                ]
            }]
        });

        let catalog = catalog(&payload).unwrap();
        assert_eq!(catalog.markets.len(), 1);
        assert_eq!(catalog.markets[0].price_tick.as_deref(), Some("0.01"));
        assert_eq!(catalog.markets[0].minimum_notional.as_deref(), Some("10"));
        assert_eq!(catalog.assets.len(), 2);
        assert_eq!(catalog.instruments[0].instrument_type, "spot");
    }
}
