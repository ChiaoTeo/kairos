//! Hyperliquid public reference gateway.

use std::collections::BTreeMap;

use serde_json::{json, Value};

use crate::application::reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceDataConnection, ReferenceEntity,
    ReferenceInstrument, ReferenceListing, ReferenceMarket,
};
use crate::application::{Connection, ConnectionSpec};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::connections::ManagedConnection;
use crate::services::drivers::http::PublicHttpClient;

pub struct HyperliquidReferenceConnection {
    connection: ManagedConnection,
    client: PublicHttpClient,
    endpoint: String,
}

impl HyperliquidReferenceConnection {
    pub fn open(endpoint: impl Into<String>) -> Result<Self, String> {
        let endpoint = endpoint.into();
        if endpoint.trim().is_empty() {
            return Err("Hyperliquid endpoint is required".into());
        }
        let spec = ConnectionSpec {
            connection_id: "reference.hyperliquid.info".into(),
            provider: "hyperliquid".into(),
            product: Some(ProductFamily::UsdMFutures),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Reference,
            credential_id: None,
            asset_type: Some(crate::domain::AssetType::Crypto),
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client: PublicHttpClient::new("kairos-reference/hyperliquid")
                .map_err(|error| error.to_string())?,
            endpoint,
        })
    }
}

impl Connection for HyperliquidReferenceConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.connection.reconnect()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}

impl ReferenceDataConnection for HyperliquidReferenceConnection {
    fn fetch_reference_catalog(&mut self) -> Result<ReferenceCatalogPayload, String> {
        self.start()?;
        let payload = self
            .client
            .post_json_with_headers(&self.endpoint, &[], &json!({"type": "metaAndAssetCtxs"}))
            .map_err(|error| error.to_string())?;
        normalize(&payload)
    }
}

pub fn normalize(payload: &Value) -> Result<ReferenceCatalogPayload, String> {
    let meta = payload
        .as_array()
        .and_then(|items| items.first())
        .ok_or_else(|| "Hyperliquid metaAndAssetCtxs response is missing metadata".to_string())?;
    let universe = meta
        .get("universe")
        .and_then(Value::as_array)
        .ok_or_else(|| "Hyperliquid metadata universe is missing".to_string())?;
    let mut assets = BTreeMap::new();
    let mut result = ReferenceCatalogPayload {
        entities: vec![ReferenceEntity {
            entity_id: "hyperliquid".into(),
            entity_type: "venue".into(),
            name: "Hyperliquid".into(),
            status: "active".into(),
        }],
        ..Default::default()
    };
    for item in universe {
        let symbol = item
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| "Hyperliquid universe item name is missing".to_string())?;
        if symbol.trim().is_empty() {
            return Err("Hyperliquid universe symbol is empty".into());
        }
        let asset_id = format!("asset:crypto:{symbol}");
        assets
            .entry(asset_id.clone())
            .or_insert_with(|| ReferenceAsset {
                asset_id: asset_id.clone(),
                code: symbol.into(),
                asset_class: "crypto".into(),
                status: "active".into(),
            });
        let instrument_id = format!("instrument:hyperliquid:{symbol}");
        let listing_id = format!("listing:hyperliquid:{symbol}");
        let market_id = format!("market:hyperliquid:{symbol}");
        result.instruments.push(ReferenceInstrument {
            instrument_id: instrument_id.clone(),
            symbol: symbol.into(),
            instrument_type: "perpetual".into(),
            product_family: Some("perpetual".into()),
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            status: "active".into(),
        });
        result.listings.push(ReferenceListing {
            listing_id: listing_id.clone(),
            instrument_id: instrument_id.clone(),
            venue_id: "hyperliquid".into(),
            venue_symbol: symbol.into(),
            status: "active".into(),
            effective_from_unix_nanos: now_nanos(),
        });
        result.markets.push(ReferenceMarket {
            market_id,
            market_key: format!("hyperliquid.perpetual.{symbol}"),
            instrument_id,
            listing_id,
            venue_id: "hyperliquid".into(),
            market_type: "perpetual".into(),
            source_symbol: symbol.into(),
            base_asset_id: Some(asset_id),
            quote_asset_id: Some("asset:fiat:USD".into()),
            status: "active".into(),
            price_tick: None,
            quantity_tick: None,
            price_precision: 0,
            quantity_precision: item
                .get("szDecimals")
                .and_then(Value::as_i64)
                .unwrap_or_default() as i32,
            minimum_quantity: None,
            minimum_notional: None,
            contract_size: None,
            effective_to_unix_nanos: None,
        });
    }
    assets
        .entry("asset:fiat:USD".into())
        .or_insert_with(|| ReferenceAsset {
            asset_id: "asset:fiat:USD".into(),
            code: "USD".into(),
            asset_class: "fiat".into(),
            status: "active".into(),
        });
    result.assets = assets.into_values().collect();
    Ok(result)
}

fn now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::normalize;

    #[test]
    fn normalizes_meta_and_asset_contexts() {
        let payload = serde_json::json!([{"universe": [{"name": "BTC", "szDecimals": 5}]}]);
        let catalog = normalize(&payload).unwrap();
        assert_eq!(catalog.markets[0].market_id, "market:hyperliquid:BTC");
        assert_eq!(catalog.markets[0].market_type, "perpetual");
    }
}
