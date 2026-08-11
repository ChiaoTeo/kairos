//! Public Reference read models used by snapshots and change events.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

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
    pub issuer_id: Option<String>,
    pub share_class: Option<String>,
    pub primary_currency_asset_id: Option<String>,
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
    pub exchange_id: String,
    pub exchange_symbol: String,
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
    pub exchange_id: String,
    pub market_type: String,
    pub asset_type: Option<String>,
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

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAccess {
    pub access_id: String,
    pub market_id: String,
    pub provider_id: String,
    pub product_family: String,
    pub provider_symbol: String,
    pub settlement_asset_id: Option<String>,
    pub status: String,
    pub effective_from_unix_nanos: u64,
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct LifecycleEvent {
    pub event_id: String,
    pub event_type: String,
    pub event_time_unix_nanos: u64,
    #[serde(default)]
    pub record_kind: Option<String>,
    #[serde(default)]
    pub record_id: Option<String>,
    pub market_id: Option<String>,
    pub instrument_id: Option<String>,
    pub listing_id: Option<String>,
    pub exchange_id: Option<String>,
    pub source_symbol: Option<String>,
    pub previous_status: Option<String>,
    pub current_status: Option<String>,
    pub previous_symbol: Option<String>,
    pub current_symbol: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceCatalog {
    pub entities: BTreeMap<String, Entity>,
    pub assets: BTreeMap<String, Asset>,
    pub instruments: BTreeMap<String, Instrument>,
    pub listings: BTreeMap<String, Listing>,
    pub markets: BTreeMap<String, Market>,
    pub financial_products: BTreeMap<String, FinancialProduct>,
    pub execution_accesses: BTreeMap<String, ExecutionAccess>,
    pub lifecycle_events: Vec<LifecycleEvent>,
    pub generation: u64,
    pub event_sequence: u64,
}

impl ReferenceCatalog {
    pub fn active_market_count(&self) -> usize {
        self.markets
            .values()
            .filter(|market| matches!(market.status.as_str(), "active" | "trading"))
            .count()
    }
}
