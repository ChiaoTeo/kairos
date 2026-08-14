//! Typed data-plane projection models decoded from Reference SQLite rows.

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceMarket {
    pub market_id: String,
    #[serde(default)]
    pub source_id: Option<String>,
    #[serde(default)]
    pub market_key: String,
    pub instrument_id: String,
    #[serde(default)]
    pub listing_id: String,
    pub exchange_id: String,
    pub market_type: String,
    #[serde(default)]
    pub asset_type: Option<String>,
    pub source_symbol: String,
    #[serde(default)]
    pub market_data_access_id: Option<String>,
    #[serde(default)]
    pub provider_symbol: Option<String>,
    #[serde(default)]
    pub base_asset_id: Option<String>,
    #[serde(default)]
    pub quote_asset_id: Option<String>,
    #[serde(default)]
    pub underlying_instrument_id: Option<String>,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub price_tick: Option<String>,
    #[serde(default)]
    pub quantity_tick: Option<String>,
    #[serde(default)]
    pub minimum_quantity: Option<String>,
    #[serde(default)]
    pub minimum_notional: Option<String>,
    #[serde(default)]
    pub price_precision: i32,
    #[serde(default)]
    pub quantity_precision: i32,
    #[serde(default)]
    pub contract_size: Option<String>,
    #[serde(default)]
    pub effective_from_unix_nanos: u64,
    #[serde(default)]
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceHealth {
    pub status: String,
    pub generation: u64,
    pub event_sequence: u64,
}
