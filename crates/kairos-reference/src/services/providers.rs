//! Provider sources and in-memory implementations for the Reference actor.

use std::collections::BTreeMap;

use kairos_integration::application::reference::{
    ReferenceCatalogPayload, ReferenceDataConnection,
};
use kairos_integration::application::{
    AccessScope, AssetType, IntegrationCapability, ProductFamily, TransportKind,
};
use kairos_integration::{ConnectionSpec, Integration, IntegrationRoute};

use crate::application::protocol::{CatalogStore, ReferenceSource};
use crate::domain::{
    Asset, Entity, FinancialProduct, Instrument, Listing, Market, ProviderCatalog,
    ReferenceCatalog, ReferenceError, ReferenceResult,
};

fn reference_spec(
    connection_id: &str,
    route: IntegrationRoute,
    product: Option<ProductFamily>,
    asset_type: Option<AssetType>,
) -> ConnectionSpec {
    ConnectionSpec {
        connection_id: connection_id.into(),
        route,
        product,
        access: AccessScope::Public,
        transport: TransportKind::Rest,
        capability: IntegrationCapability::Reference,
        credential_id: None,
        asset_type,
    }
}

pub(crate) fn reference_spec_for_public(
    route: IntegrationRoute,
    product: ProductFamily,
    asset_type: Option<AssetType>,
) -> ConnectionSpec {
    reference_spec("reference.public", route, Some(product), asset_type)
}

/// Binance Spot public reference source.
///
/// The provider connection and vendor normalization live in integration. This
/// source only maps the neutral integration payload into Reference-owned
/// domain records.
pub struct BinanceSpotSource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct BinanceOptionsSource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct BinanceEquitySource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct MassiveSource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct MassiveEquitySource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct HyperliquidSource {
    connection: Box<dyn ReferenceDataConnection>,
}

pub struct PublicSource {
    id: String,
    connection: Box<dyn ReferenceDataConnection>,
}

/// Reference-owned fan-in for a workspace catalog. Each provider remains an
/// independent integration connection; this source only merges normalized
/// records before they enter the Reference actor.
pub struct CompositeSource {
    sources: Vec<Box<dyn ReferenceSource>>,
}

impl CompositeSource {
    pub fn new(sources: Vec<Box<dyn ReferenceSource>>) -> ReferenceResult<Self> {
        if sources.is_empty() {
            return Err(ReferenceError::Provider(
                "workspace reference catalog has no sources".into(),
            ));
        }
        Ok(Self { sources })
    }
}

impl ReferenceSource for CompositeSource {
    fn source_id(&self) -> &str {
        "workspace"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let mut entities = BTreeMap::new();
        let mut assets = BTreeMap::new();
        let mut instruments = BTreeMap::new();
        let mut listings = BTreeMap::new();
        let mut markets = BTreeMap::new();
        let mut financial_products = BTreeMap::new();
        for source in &mut self.sources {
            let catalog = source.fetch_catalog()?;
            for value in catalog.entities {
                entities.insert(value.entity_id.clone(), value);
            }
            for value in catalog.assets {
                assets.insert(value.asset_id.clone(), value);
            }
            for value in catalog.instruments {
                instruments.insert(value.instrument_id.clone(), value);
            }
            for value in catalog.listings {
                listings.insert(value.listing_id.clone(), value);
            }
            for value in catalog.markets {
                markets.insert(value.market_id.clone(), value);
            }
            for value in catalog.financial_products {
                financial_products.insert(value.product_id.clone(), value);
            }
        }
        Ok(ProviderCatalog {
            entities: entities.into_values().collect(),
            assets: assets.into_values().collect(),
            instruments: instruments.into_values().collect(),
            listings: listings.into_values().collect(),
            markets: markets.into_values().collect(),
            financial_products: financial_products.into_values().collect(),
        })
    }
}

impl PublicSource {
    pub fn new(id: impl Into<String>, connection: Box<dyn ReferenceDataConnection>) -> Self {
        Self {
            id: id.into(),
            connection,
        }
    }
}

impl ReferenceSource for PublicSource {
    fn source_id(&self) -> &str {
        &self.id
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl BinanceSpotSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new()
            .with_binance_reference(endpoint)
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.binance.spot.rest",
                IntegrationRoute::exchange("binance"),
                Some(ProductFamily::Spot),
                None,
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }

    pub fn production() -> ReferenceResult<Self> {
        Self::new(crate::composition::default_endpoint("binance-spot"))
    }
}

impl BinanceOptionsSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new()
            .with_binance_options_reference(endpoint)
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.binance.options.rest",
                IntegrationRoute::exchange("binance"),
                Some(ProductFamily::Options),
                None,
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl BinanceEquitySource {
    pub fn new(api_key: impl Into<String>, secret: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new().with_binance_equity(api_key, secret);
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.binance.equity.rest",
                IntegrationRoute::exchange("binance"),
                Some(ProductFamily::Equity),
                Some(AssetType::Equity),
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl MassiveSource {
    pub fn new(api_key: impl Into<String>, base_url: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new()
            .with_massive_reference(api_key, base_url)
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.massive.market",
                IntegrationRoute::data_provider("massive"),
                None,
                Some(AssetType::Equity),
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl MassiveEquitySource {
    pub fn new(api_key: impl Into<String>, base_url: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new()
            .with_massive_equity_reference(api_key, base_url)
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.massive.equity",
                IntegrationRoute::data_provider("massive"),
                Some(ProductFamily::Equity),
                Some(AssetType::Equity),
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl HyperliquidSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        let integration = Integration::new().with_hyperliquid_reference(endpoint);
        let connection = integration
            .connect_reference(&reference_spec(
                "reference.hyperliquid.info",
                IntegrationRoute::exchange("hyperliquid"),
                Some(ProductFamily::UsdMFutures),
                Some(AssetType::Crypto),
            ))
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self { connection })
    }
}

impl ReferenceSource for BinanceSpotSource {
    fn source_id(&self) -> &str {
        "binance-spot"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl ReferenceSource for BinanceOptionsSource {
    fn source_id(&self) -> &str {
        "binance-options"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl ReferenceSource for BinanceEquitySource {
    fn source_id(&self) -> &str {
        "binance-equity"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl ReferenceSource for MassiveSource {
    fn source_id(&self) -> &str {
        "massive-options"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl ReferenceSource for MassiveEquitySource {
    fn source_id(&self) -> &str {
        "massive-equity"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

impl ReferenceSource for HyperliquidSource {
    fn source_id(&self) -> &str {
        "hyperliquid"
    }

    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let payload = self
            .connection
            .fetch_reference_catalog()
            .map_err(ReferenceError::Provider)?;
        provider_catalog_from_integration(payload)
    }
}

fn provider_catalog_from_integration(
    payload: ReferenceCatalogPayload,
) -> ReferenceResult<ProviderCatalog> {
    Ok(ProviderCatalog {
        entities: payload
            .entities
            .into_iter()
            .map(|value| Entity {
                entity_id: value.entity_id,
                entity_type: value.entity_type,
                name: value.name,
                status: value.status,
            })
            .collect(),
        assets: payload
            .assets
            .into_iter()
            .map(|value| Asset {
                asset_id: value.asset_id,
                code: value.code,
                asset_class: value.asset_class,
                status: value.status,
                ..Asset::default()
            })
            .collect(),
        instruments: payload
            .instruments
            .into_iter()
            .map(|value| Instrument {
                instrument_id: value.instrument_id,
                symbol: value.symbol,
                instrument_type: value.instrument_type,
                product_family: value.product_family,
                underlying_instrument_id: value.underlying_instrument_id,
                expiry_unix_nanos: value.expiry_unix_nanos,
                strike: value.strike,
                option_right: value.option_right,
                status: value.status,
                ..Instrument::default()
            })
            .collect(),
        listings: payload
            .listings
            .into_iter()
            .map(|value| Listing {
                listing_id: value.listing_id,
                instrument_id: value.instrument_id,
                venue_id: value.venue_id,
                venue_symbol: value.venue_symbol,
                status: value.status,
                effective_from_unix_nanos: value.effective_from_unix_nanos,
                ..Listing::default()
            })
            .collect(),
        markets: payload
            .markets
            .into_iter()
            .map(|value| Market {
                market_id: value.market_id,
                market_key: value.market_key,
                instrument_id: value.instrument_id,
                listing_id: value.listing_id,
                venue_id: value.venue_id,
                market_type: value.market_type,
                asset_type: value.asset_type,
                source_symbol: value.source_symbol,
                base_asset_id: value.base_asset_id,
                quote_asset_id: value.quote_asset_id,
                status: value.status,
                price_tick: value.price_tick,
                quantity_tick: value.quantity_tick,
                price_precision: value.price_precision,
                quantity_precision: value.quantity_precision,
                minimum_quantity: value.minimum_quantity,
                minimum_notional: value.minimum_notional,
                contract_size: value.contract_size,
                effective_to_unix_nanos: value.effective_to_unix_nanos,
                ..Market::default()
            })
            .collect(),
        financial_products: payload
            .financial_products
            .into_iter()
            .map(|value| FinancialProduct {
                product_id: value.product_id,
                product_type: value.product_type,
                name: value.name,
                asset_id: value.asset_id,
                provider_product_id: value.provider_product_id,
                provider_id: value.provider_id,
                issuer_id: value.issuer_id,
                currency_asset_id: value.currency_asset_id,
                min_amount: value.min_amount,
                max_amount: value.max_amount,
                apr: value.apr,
                lock_period_days: value.lock_period_days,
                maturity_at_unix_nanos: value.maturity_at_unix_nanos,
                status: value.status,
                effective_from_unix_nanos: value.effective_from_unix_nanos,
                effective_to_unix_nanos: value.effective_to_unix_nanos,
            })
            .collect(),
    })
}

pub struct MemoryCatalogStore(pub Option<ReferenceCatalog>);

impl CatalogStore for MemoryCatalogStore {
    fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
        Ok(self.0.clone())
    }

    fn save(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<()> {
        self.0 = Some(catalog.clone());
        Ok(())
    }
}
