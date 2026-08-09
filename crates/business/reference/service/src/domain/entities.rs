//! Reference domain entities and provider snapshots.

use serde::{Deserialize, Serialize};

use super::{ReferenceError, ReferenceResult};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProviderHealth {
    pub source_id: String,
    pub status: String,
    pub last_attempt_unix_nanos: Option<u64>,
    pub last_success_unix_nanos: Option<u64>,
    pub consecutive_failures: u32,
    pub stale: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Entity {
    pub entity_id: String,
    pub entity_type: String,
    pub name: String,
    pub status: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Asset {
    pub asset_id: String,
    pub code: String,
    pub name: Option<String>,
    pub asset_class: String,
    pub status: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Instrument {
    pub instrument_id: String,
    pub symbol: String,
    pub name: Option<String>,
    pub instrument_type: String,
    pub product_family: Option<String>,
    pub underlying_instrument_id: Option<String>,
    pub expiry_unix_nanos: Option<u64>,
    pub strike: Option<String>,
    pub option_right: Option<String>,
    pub status: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Listing {
    pub listing_id: String,
    pub instrument_id: String,
    pub venue_id: String,
    pub venue_symbol: String,
    pub status: String,
    pub effective_from_unix_nanos: u64,
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Market {
    pub market_id: String,
    pub market_key: String,
    pub instrument_id: String,
    pub listing_id: String,
    pub venue_id: String,
    pub market_type: String,
    #[serde(default)]
    pub asset_type: Option<String>,
    #[serde(default)]
    pub underlying_instrument_id: Option<String>,
    pub source_symbol: String,
    pub base_asset_id: Option<String>,
    pub quote_asset_id: Option<String>,
    pub status: String,
    pub price_tick: Option<String>,
    pub quantity_tick: Option<String>,
    pub price_precision: i32,
    pub quantity_precision: i32,
    pub minimum_quantity: Option<String>,
    pub minimum_notional: Option<String>,
    pub contract_size: Option<String>,
    pub effective_from_unix_nanos: u64,
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct LifecycleEvent {
    pub event_id: String,
    pub event_type: String,
    pub event_time_unix_nanos: u64,
    pub market_id: Option<String>,
    pub instrument_id: Option<String>,
    pub listing_id: Option<String>,
    pub venue_id: Option<String>,
    pub source_symbol: Option<String>,
    pub previous_status: Option<String>,
    pub current_status: Option<String>,
    pub previous_symbol: Option<String>,
    pub current_symbol: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct FinancialProduct {
    pub product_id: String,
    pub product_type: String,
    pub name: String,
    pub asset_id: String,
    pub provider_product_id: String,
    pub provider_id: Option<String>,
    pub issuer_id: Option<String>,
    pub currency_asset_id: Option<String>,
    pub min_amount: Option<String>,
    pub max_amount: Option<String>,
    pub apr: Option<String>,
    pub lock_period_days: i32,
    pub maturity_at_unix_nanos: Option<u64>,
    pub status: String,
    pub effective_from_unix_nanos: u64,
    pub effective_to_unix_nanos: Option<u64>,
}

/// A provider-specific execution path for a canonical instrument.
///
/// ExecutionAccess is not a second market. It records how an instrument can
/// be reached for order entry when the execution provider is not necessarily
/// the primary venue that defines its market price.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAccess {
    pub access_id: String,
    pub instrument_id: String,
    pub provider_id: String,
    pub product_family: String,
    pub provider_symbol: String,
    pub settlement_asset_id: Option<String>,
    pub status: String,
    pub effective_from_unix_nanos: u64,
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProviderCatalog {
    pub entities: Vec<Entity>,
    pub assets: Vec<Asset>,
    pub instruments: Vec<Instrument>,
    pub listings: Vec<Listing>,
    pub markets: Vec<Market>,
    pub financial_products: Vec<FinancialProduct>,
    pub execution_accesses: Vec<ExecutionAccess>,
}

impl ProviderCatalog {
    /// Validate the provider boundary before any actor-owned state is changed.
    pub fn validate(&self) -> ReferenceResult<()> {
        fn unique<T, F>(values: &[T], label: &str, key: F) -> ReferenceResult<()>
        where
            F: Fn(&T) -> &str,
        {
            let mut ids = std::collections::BTreeSet::new();
            for value in values {
                let id = key(value);
                if id.is_empty() {
                    return Err(ReferenceError::Invalid(format!("{label} id is empty")));
                }
                if !ids.insert(id) {
                    return Err(ReferenceError::Invalid(format!(
                        "duplicate {label} id: {id}"
                    )));
                }
            }
            Ok(())
        }

        unique(&self.entities, "entity", |value| &value.entity_id)?;
        unique(&self.assets, "asset", |value| &value.asset_id)?;
        unique(&self.instruments, "instrument", |value| {
            &value.instrument_id
        })?;
        unique(&self.listings, "listing", |value| &value.listing_id)?;
        unique(&self.markets, "market", |value| &value.market_id)?;
        unique(&self.financial_products, "financial product", |value| {
            &value.product_id
        })?;
        unique(&self.execution_accesses, "execution access", |value| {
            &value.access_id
        })?;

        let instrument_ids: std::collections::BTreeSet<_> = self
            .instruments
            .iter()
            .map(|value| value.instrument_id.as_str())
            .collect();
        let listing_ids: std::collections::BTreeSet<_> = self
            .listings
            .iter()
            .map(|value| value.listing_id.as_str())
            .collect();
        let entity_ids: std::collections::BTreeSet<_> = self
            .entities
            .iter()
            .map(|value| value.entity_id.as_str())
            .collect();
        let asset_ids: std::collections::BTreeSet<_> = self
            .assets
            .iter()
            .map(|value| value.asset_id.as_str())
            .collect();
        for listing in &self.listings {
            if !instrument_ids.contains(listing.instrument_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "listing {} references missing instrument {}",
                    listing.listing_id, listing.instrument_id
                )));
            }
            if !entity_ids.contains(listing.venue_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "listing {} references missing venue {}",
                    listing.listing_id, listing.venue_id
                )));
            }
        }
        for market in &self.markets {
            if !instrument_ids.contains(market.instrument_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "market {} references missing instrument {}",
                    market.market_id, market.instrument_id
                )));
            }
            if !listing_ids.contains(market.listing_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "market {} references missing listing {}",
                    market.market_id, market.listing_id
                )));
            }
            if !entity_ids.contains(market.venue_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "market {} references missing venue {}",
                    market.market_id, market.venue_id
                )));
            }
        }
        for access in &self.execution_accesses {
            if !instrument_ids.contains(access.instrument_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "execution access {} references missing instrument {}",
                    access.access_id, access.instrument_id
                )));
            }
            if let Some(asset_id) = access.settlement_asset_id.as_deref() {
                if !asset_ids.contains(asset_id) {
                    return Err(ReferenceError::Invalid(format!(
                        "execution access {} references missing settlement asset {}",
                        access.access_id, asset_id
                    )));
                }
            }
            if access
                .effective_to_unix_nanos
                .is_some_and(|end| end <= access.effective_from_unix_nanos)
            {
                return Err(ReferenceError::Invalid(format!(
                    "execution access {} has an invalid effective interval",
                    access.access_id
                )));
            }
        }
        for product in &self.financial_products {
            if !asset_ids.contains(product.asset_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "financial product {} references missing asset {}",
                    product.product_id, product.asset_id
                )));
            }
            if let Some(currency_asset_id) = product.currency_asset_id.as_deref() {
                if !asset_ids.contains(currency_asset_id) {
                    return Err(ReferenceError::Invalid(format!(
                        "financial product {} references missing currency asset {}",
                        product.product_id, currency_asset_id
                    )));
                }
            }
            if product
                .effective_to_unix_nanos
                .is_some_and(|end| end <= product.effective_from_unix_nanos)
            {
                return Err(ReferenceError::Invalid(format!(
                    "financial product {} has an invalid effective interval",
                    product.product_id
                )));
            }
        }
        Ok(())
    }
}
