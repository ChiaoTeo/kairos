//! Provider-neutral reference data returned by reference connections.

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceCatalogPayload {
    pub entities: Vec<ReferenceEntity>,
    pub assets: Vec<ReferenceAsset>,
    pub instruments: Vec<ReferenceInstrument>,
    pub listings: Vec<ReferenceListing>,
    pub markets: Vec<ReferenceMarket>,
    pub financial_products: Vec<ReferenceFinancialProduct>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceEntity {
    pub entity_id: String,
    pub entity_type: String,
    pub name: String,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceAsset {
    pub asset_id: String,
    pub code: String,
    pub asset_class: String,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceInstrument {
    pub instrument_id: String,
    pub symbol: String,
    pub instrument_type: String,
    pub product_family: Option<String>,
    pub underlying_instrument_id: Option<String>,
    pub expiry_unix_nanos: Option<u64>,
    pub strike: Option<String>,
    pub option_right: Option<String>,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceListing {
    pub listing_id: String,
    pub instrument_id: String,
    pub venue_id: String,
    pub venue_symbol: String,
    pub status: String,
    pub effective_from_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceMarket {
    pub market_id: String,
    pub market_key: String,
    pub instrument_id: String,
    pub listing_id: String,
    pub venue_id: String,
    pub market_type: String,
    pub asset_type: Option<String>,
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
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceFinancialProduct {
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
