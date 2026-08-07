//! Public instrument catalog connections for products whose metadata endpoint
//! is not otherwise needed by a provider-specific business module.

use crate::application::reference::{ReferenceCatalogPayload, ReferenceDataConnection};
use crate::application::{Connection, ConnectionSpec};
use crate::domain::{
    AccessScope, AssetType, ConnectionHealth, ConnectionIdentity, ConnectionState,
    IntegrationCapability, ProductFamily, TransportKind,
};
use crate::services::connections::ManagedConnection;
use crate::services::drivers::http::PublicHttpClient;
use serde_json::Value;

pub struct PublicReferenceConnection {
    connection: ManagedConnection,
    http: PublicHttpClient,
    product: ProductFamily,
    asset_type: Option<AssetType>,
    endpoint: String,
    normalizer: fn(&Value, ProductFamily) -> Result<ReferenceCatalogPayload, String>,
}

impl PublicReferenceConnection {
    pub fn new(
        provider: impl Into<String>,
        product: ProductFamily,
        asset_type: Option<AssetType>,
        endpoint: impl Into<String>,
        normalizer: fn(&Value, ProductFamily) -> Result<ReferenceCatalogPayload, String>,
    ) -> Result<Self, String> {
        let provider = provider.into();
        let endpoint = endpoint.into();
        if provider.trim().is_empty() || endpoint.trim().is_empty() {
            return Err("public reference provider and endpoint are required".into());
        }
        let connection = ManagedConnection::new(
            ConnectionSpec {
                connection_id: format!("reference.{provider}.{:?}.rest", product),
                provider: provider.clone(),
                product: Some(product),
                access: AccessScope::Public,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::Reference,
                credential_id: None,
                asset_type,
            },
            Vec::new(),
        )?;
        Ok(Self {
            connection,
            http: PublicHttpClient::new("kairos-integration/public-reference")
                .map_err(|error| error.to_string())?,
            product,
            asset_type,
            endpoint,
            normalizer,
        })
    }
}

impl Connection for PublicReferenceConnection {
    fn identity(&self) -> &ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &ConnectionState {
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
    fn health(&self) -> ConnectionHealth {
        self.connection.health()
    }
}

impl ReferenceDataConnection for PublicReferenceConnection {
    fn fetch_reference_catalog(&mut self) -> Result<ReferenceCatalogPayload, String> {
        self.start()?;
        let payload = self
            .http
            .get_json(&self.endpoint)
            .map_err(|error| error.to_string())?;
        let mut catalog = (self.normalizer)(&payload, self.product)?;
        if let Some(asset_type) = self.asset_type {
            let value = match asset_type {
                AssetType::Crypto => "crypto",
                AssetType::Equity => "equity",
                AssetType::Other => "other",
            };
            for market in &mut catalog.markets {
                market.asset_type = Some(value.into());
            }
        }
        Ok(catalog)
    }
}

pub fn binance_derivatives_catalog(
    payload: &Value,
    product: ProductFamily,
) -> Result<ReferenceCatalogPayload, String> {
    let symbols = payload
        .get("symbols")
        .and_then(Value::as_array)
        .ok_or_else(|| "Binance derivatives exchangeInfo.symbols is missing".to_string())?;
    let family = match product {
        ProductFamily::UsdMFutures => "usd-m-futures",
        ProductFamily::CoinMFutures => "coin-m-futures",
        _ => return Err("Binance derivatives reference requires futures product".into()),
    };
    let mut result = ReferenceCatalogPayload {
        entities: vec![crate::application::reference::ReferenceEntity {
            entity_id: "binance".into(),
            entity_type: "venue".into(),
            name: "Binance".into(),
            status: "active".into(),
        }],
        ..Default::default()
    };
    for row in symbols {
        let symbol = text(row, "symbol").ok_or("Binance derivative symbol is missing")?;
        let status = if text(row, "status").as_deref() == Some("TRADING") {
            "active"
        } else {
            "inactive"
        };
        let instrument_id = format!("instrument:binance:{family}:{symbol}");
        let listing_id = format!("listing:binance:{family}:{symbol}");
        result
            .instruments
            .push(crate::application::reference::ReferenceInstrument {
                instrument_id: instrument_id.clone(),
                symbol: symbol.clone(),
                instrument_type: "future".into(),
                product_family: Some(family.into()),
                underlying_instrument_id: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                status: status.into(),
            });
        result
            .listings
            .push(crate::application::reference::ReferenceListing {
                listing_id: listing_id.clone(),
                instrument_id: instrument_id.clone(),
                venue_id: "binance".into(),
                venue_symbol: symbol.clone(),
                status: status.into(),
                effective_from_unix_nanos: 0,
            });
        result
            .markets
            .push(crate::application::reference::ReferenceMarket {
                market_id: format!("market:binance:{family}:{symbol}"),
                market_key: format!("binance.{family}.{symbol}"),
                instrument_id,
                listing_id,
                venue_id: "binance".into(),
                market_type: family.into(),
                source_symbol: symbol,
                asset_type: Some("crypto".into()),
                base_asset_id: None,
                quote_asset_id: None,
                status: status.into(),
                price_tick: None,
                quantity_tick: None,
                price_precision: 0,
                quantity_precision: 0,
                minimum_quantity: None,
                minimum_notional: None,
                contract_size: text(row, "contractSize"),
                effective_to_unix_nanos: None,
            });
    }
    Ok(result)
}

pub fn okx_catalog(
    payload: &Value,
    product: ProductFamily,
) -> Result<ReferenceCatalogPayload, String> {
    let rows = payload
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| "OKX instruments response data is missing".to_string())?;
    let family = match product {
        ProductFamily::Spot => "spot",
        ProductFamily::UsdMFutures => "swap",
        ProductFamily::CoinMFutures => "futures",
        ProductFamily::Options => "options",
        _ => return Err("unsupported OKX reference product".into()),
    };
    let asset_class = if product == ProductFamily::Spot {
        "crypto"
    } else {
        "crypto"
    };
    let mut result = ReferenceCatalogPayload {
        entities: vec![crate::application::reference::ReferenceEntity {
            entity_id: "okx".into(),
            entity_type: "venue".into(),
            name: "OKX".into(),
            status: "active".into(),
        }],
        ..Default::default()
    };
    for row in rows {
        let symbol = text(row, "instId").ok_or("OKX instrument id is missing")?;
        let status = if text(row, "state").as_deref() == Some("live") {
            "active"
        } else {
            "inactive"
        };
        let instrument_id = format!("instrument:okx:{family}:{symbol}");
        let listing_id = format!("listing:okx:{family}:{symbol}");
        let base = text(row, "baseCcy");
        let quote = text(row, "quoteCcy");
        if let Some(code) = base.clone() {
            result
                .assets
                .push(crate::application::reference::ReferenceAsset {
                    asset_id: format!("asset:{asset_class}:{code}"),
                    code,
                    asset_class: asset_class.into(),
                    status: "active".into(),
                });
        }
        if let Some(code) = quote.clone() {
            result
                .assets
                .push(crate::application::reference::ReferenceAsset {
                    asset_id: format!("asset:{asset_class}:{code}"),
                    code,
                    asset_class: asset_class.into(),
                    status: "active".into(),
                });
        }
        result
            .instruments
            .push(crate::application::reference::ReferenceInstrument {
                instrument_id: instrument_id.clone(),
                symbol: symbol.clone(),
                instrument_type: family.into(),
                product_family: Some(family.into()),
                underlying_instrument_id: None,
                expiry_unix_nanos: None,
                strike: text(row, "stk"),
                option_right: text(row, "optType"),
                status: status.into(),
            });
        result
            .listings
            .push(crate::application::reference::ReferenceListing {
                listing_id: listing_id.clone(),
                instrument_id: instrument_id.clone(),
                venue_id: "okx".into(),
                venue_symbol: symbol.clone(),
                status: status.into(),
                effective_from_unix_nanos: 0,
            });
        result
            .markets
            .push(crate::application::reference::ReferenceMarket {
                market_id: format!("market:okx:{family}:{symbol}"),
                market_key: format!("okx.{family}.{symbol}"),
                instrument_id,
                listing_id,
                venue_id: "okx".into(),
                market_type: family.into(),
                source_symbol: symbol,
                asset_type: Some("crypto".into()),
                base_asset_id: base.map(|v| format!("asset:{asset_class}:{v}")),
                quote_asset_id: quote.map(|v| format!("asset:{asset_class}:{v}")),
                status: status.into(),
                price_tick: text(row, "tickSz"),
                quantity_tick: text(row, "lotSz"),
                price_precision: 0,
                quantity_precision: 0,
                minimum_quantity: text(row, "minSz"),
                minimum_notional: None,
                contract_size: text(row, "ctVal"),
                effective_to_unix_nanos: None,
            });
    }
    result.assets.sort_by(|a, b| a.asset_id.cmp(&b.asset_id));
    result.assets.dedup_by(|a, b| a.asset_id == b.asset_id);
    Ok(result)
}

fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
}
