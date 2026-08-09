//! Binance Stocks Trading reference normalization.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::application::reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceEntity, ReferenceExecutionAccess,
    ReferenceInstrument, ReferenceListing, ReferenceMarket,
};

pub fn catalog(payload: &Value) -> Result<ReferenceCatalogPayload, String> {
    let symbols = payload
        .get("symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance Equity exchangeInfo.symbols is missing".to_string())?;
    let mut assets = BTreeMap::new();
    assets.insert(
        "asset:fiat:USD".into(),
        ReferenceAsset {
            asset_id: "asset:fiat:USD".into(),
            code: "USD".into(),
            asset_class: "fiat".into(),
            status: "active".into(),
        },
    );
    assets.insert(
        "asset:crypto:USDC".into(),
        ReferenceAsset {
            asset_id: "asset:crypto:USDC".into(),
            code: "USDC".into(),
            asset_class: "crypto".into(),
            status: "active".into(),
        },
    );
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
            "halted".to_string()
        } else {
            "active".to_string()
        };
        let asset_id = format!("asset:equity:{}", symbol.to_ascii_uppercase());
        assets
            .entry(asset_id.clone())
            .or_insert_with(|| ReferenceAsset {
                asset_id: asset_id.clone(),
                code: symbol.to_ascii_uppercase(),
                asset_class: "equity".into(),
                status: status.clone(),
            });
        let key = symbol.to_ascii_uppercase();
        let instrument_id = format!("instrument:equity:{key}");
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
            status: status.clone(),
        });
        result.listings.push(ReferenceListing {
            listing_id: listing_id.clone(),
            instrument_id: instrument_id.clone(),
            venue_id: "binance".into(),
            venue_symbol: key.clone(),
            status: status.clone(),
            effective_from_unix_nanos: now_unix_nanos(),
        });
        result.markets.push(ReferenceMarket {
            market_id: format!("market:binance:equity:{key}"),
            market_key: format!("binance.equity.{key}"),
            instrument_id: instrument_id.clone(),
            listing_id,
            venue_id: "binance".into(),
            market_type: "equity".into(),
            asset_type: Some("equity".into()),
            source_symbol: key.clone(),
            base_asset_id: Some(asset_id),
            quote_asset_id: Some("asset:fiat:USD".into()),
            status: status.clone(),
            price_tick: None,
            quantity_tick: string(value, "stepSize"),
            price_precision: 0,
            quantity_precision: 0,
            minimum_quantity: string(value, "minQty"),
            minimum_notional: string(value, "minNotional"),
            contract_size: None,
            effective_to_unix_nanos: None,
        });
        result.execution_accesses.push(ReferenceExecutionAccess {
            access_id: format!("access:binance:equity:{key}"),
            instrument_id: instrument_id.clone(),
            provider_id: "binance".into(),
            product_family: "equity".into(),
            provider_symbol: key,
            settlement_asset_id: Some("asset:crypto:USDC".into()),
            status: status.clone(),
            effective_from_unix_nanos: now_unix_nanos(),
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
        assert_eq!(
            result.instruments[0].instrument_id,
            "instrument:equity:AAPL"
        );
        assert_eq!(result.markets[0].source_symbol, "AAPL");
        assert_eq!(
            result.markets[0].quote_asset_id.as_deref(),
            Some("asset:fiat:USD")
        );
        assert_eq!(result.markets[0].quantity_tick.as_deref(), Some("1"));
        assert_eq!(result.execution_accesses.len(), 1);
        assert_eq!(
            result.execution_accesses[0].settlement_asset_id.as_deref(),
            Some("asset:crypto:USDC")
        );
        assert_eq!(result.execution_accesses[0].provider_symbol, "AAPL");
    }

    #[test]
    fn rejects_stocks_exchange_info_without_symbols() {
        assert!(catalog(&serde_json::json!({})).is_err());
    }
}
