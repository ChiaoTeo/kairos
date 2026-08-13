//! Reference domain entities and provider snapshots.

use kairos_domain_types::{
    AssetId, Exchange, ExecutionAccessId, InstrumentId, IssuerId, ListingId, MarketId,
    ProviderSymbol, Rate, ReferenceStatus, Symbol, UnixNanos,
};
use serde::{Deserialize, Serialize};

use super::{ReferenceError, ReferenceResult};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProviderHealth {
    pub source_id: String,
    pub status: String,
    pub last_attempt_unix_nanos: Option<UnixNanos>,
    pub last_success_unix_nanos: Option<UnixNanos>,
    pub consecutive_failures: u32,
    pub stale: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Entity {
    #[serde(default)]
    pub source_id: Option<String>,
    pub entity_id: String,
    pub entity_type: String,
    pub name: String,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Asset {
    #[serde(default)]
    pub source_id: Option<String>,
    pub asset_id: AssetId,
    pub code: String,
    pub name: Option<String>,
    pub asset_class: String,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Instrument {
    #[serde(default)]
    pub source_id: Option<String>,
    pub instrument_id: InstrumentId,
    pub symbol: Symbol,
    pub name: Option<String>,
    pub instrument_type: String,
    pub product_family: Option<String>,
    #[serde(default)]
    pub issuer_id: Option<IssuerId>,
    #[serde(default)]
    pub share_class: Option<String>,
    #[serde(default)]
    pub primary_currency_asset_id: Option<AssetId>,
    pub underlying_instrument_id: Option<InstrumentId>,
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<String>,
    pub option_right: Option<String>,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Listing {
    #[serde(default)]
    pub source_id: Option<String>,
    pub listing_id: ListingId,
    pub instrument_id: InstrumentId,
    pub exchange_id: Exchange,
    pub exchange_symbol: Symbol,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Market {
    #[serde(default)]
    pub source_id: Option<String>,
    pub market_id: MarketId,
    pub market_key: String,
    pub instrument_id: InstrumentId,
    pub listing_id: ListingId,
    pub exchange_id: Exchange,
    pub market_type: String,
    #[serde(default)]
    pub asset_type: Option<String>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    pub source_symbol: Symbol,
    pub base_asset_id: Option<AssetId>,
    pub quote_asset_id: Option<AssetId>,
    pub status: ReferenceStatus,
    pub price_tick: Option<String>,
    pub quantity_tick: Option<String>,
    pub price_precision: i32,
    pub quantity_precision: i32,
    pub minimum_quantity: Option<String>,
    pub minimum_notional: Option<String>,
    pub contract_size: Option<String>,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct LifecycleEvent {
    pub event_id: String,
    pub event_type: String,
    pub event_time_unix_nanos: UnixNanos,
    #[serde(default)]
    pub record_kind: Option<String>,
    #[serde(default)]
    pub record_id: Option<String>,
    pub market_id: Option<MarketId>,
    pub instrument_id: Option<InstrumentId>,
    pub listing_id: Option<ListingId>,
    pub exchange_id: Option<Exchange>,
    pub source_symbol: Option<Symbol>,
    pub previous_status: Option<ReferenceStatus>,
    pub current_status: Option<ReferenceStatus>,
    pub previous_symbol: Option<String>,
    pub current_symbol: Option<String>,
    #[serde(default)]
    pub operation: Option<String>,
    #[serde(default)]
    pub generation: u64,
    #[serde(default)]
    pub record_payload_json: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct FinancialProduct {
    #[serde(default)]
    pub source_id: Option<String>,
    pub product_id: String,
    pub product_type: String,
    pub name: String,
    pub asset_id: AssetId,
    pub provider_product_id: String,
    pub provider_id: Option<String>,
    pub issuer_id: Option<IssuerId>,
    pub currency_asset_id: Option<AssetId>,
    pub min_amount: Option<String>,
    pub max_amount: Option<String>,
    pub apr: Option<String>,
    pub lock_period_days: i32,
    pub maturity_at_unix_nanos: Option<UnixNanos>,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

/// A provider-specific execution path for a canonical instrument.
///
/// ExecutionAccess is not a second market. It records how an instrument can
/// be reached for order entry when the execution provider is not necessarily
/// the primary exchange that defines its market price.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAccess {
    #[serde(default)]
    pub source_id: Option<String>,
    pub access_id: ExecutionAccessId,
    pub market_id: MarketId,
    pub provider_id: String,
    pub product_family: String,
    pub provider_symbol: ProviderSymbol,
    pub settlement_asset_id: Option<AssetId>,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

impl Default for ExecutionAccess {
    fn default() -> Self {
        Self {
            source_id: None,
            access_id: ExecutionAccessId::new("access:default").expect("valid access ID"),
            market_id: MarketId::new("market:default").expect("valid market ID"),
            provider_id: String::new(),
            product_family: String::new(),
            provider_symbol: ProviderSymbol::new("symbol:default").expect("valid provider symbol"),
            settlement_asset_id: None,
            status: ReferenceStatus::Unknown,
            effective_from_unix_nanos: UnixNanos::default(),
            effective_to_unix_nanos: None,
        }
    }
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
        fn required(value: &str, label: &str) -> ReferenceResult<()> {
            if value.trim().is_empty() {
                return Err(ReferenceError::Invalid(format!("{label} is empty")));
            }
            Ok(())
        }

        fn interval(from: UnixNanos, to: Option<UnixNanos>, label: &str) -> ReferenceResult<()> {
            if to.is_some_and(|end| end <= from) {
                return Err(ReferenceError::Invalid(format!(
                    "{label} has an invalid effective interval"
                )));
            }
            Ok(())
        }

        fn non_negative_decimal(value: Option<&str>, label: &str) -> ReferenceResult<()> {
            if let Some(value) = value {
                required(value, label)?;
                let decimal = value.trim().parse::<Rate>().map_err(|_| {
                    ReferenceError::Invalid(format!("{label} is not a valid decimal"))
                })?;
                if decimal < Rate::ZERO {
                    return Err(ReferenceError::Invalid(format!(
                        "{label} must not be negative"
                    )));
                }
            }
            Ok(())
        }

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

        for entity in &self.entities {
            required(
                &entity.entity_type,
                &format!("entity {} type", entity.entity_id),
            )?;
            required(&entity.name, &format!("entity {} name", entity.entity_id))?;
            required(
                entity.status.as_str(),
                &format!("entity {} status", entity.entity_id),
            )?;
        }
        for asset in &self.assets {
            required(&asset.code, &format!("asset {} code", asset.asset_id))?;
            required(
                &asset.asset_class,
                &format!("asset {} class", asset.asset_id),
            )?;
            required(
                asset.status.as_str(),
                &format!("asset {} status", asset.asset_id),
            )?;
        }
        for instrument in &self.instruments {
            required(
                &instrument.symbol,
                &format!("instrument {} symbol", instrument.instrument_id),
            )?;
            required(
                &instrument.instrument_type,
                &format!("instrument {} type", instrument.instrument_id),
            )?;
            required(
                instrument.status.as_str(),
                &format!("instrument {} status", instrument.instrument_id),
            )?;
            non_negative_decimal(
                instrument.strike.as_deref(),
                &format!("instrument {} strike", instrument.instrument_id),
            )?;
            if matches!(
                instrument.instrument_type.to_ascii_lowercase().as_str(),
                "option" | "options"
            ) && (instrument.expiry_unix_nanos.is_none()
                || instrument.strike.is_none()
                || !matches!(
                    instrument
                        .option_right
                        .as_deref()
                        .map(|value| value.to_ascii_lowercase())
                        .as_deref(),
                    Some("call" | "put" | "c" | "p")
                ))
            {
                return Err(ReferenceError::Invalid(format!(
                    "option instrument {} requires expiry, strike and call/put right",
                    instrument.instrument_id
                )));
            }
            if let Some(underlying_id) = instrument.underlying_instrument_id.as_deref() {
                if underlying_id == instrument.instrument_id.as_str() {
                    return Err(ReferenceError::Invalid(format!(
                        "instrument {} cannot underlie itself",
                        instrument.instrument_id
                    )));
                }
                if !self
                    .instruments
                    .iter()
                    .any(|value| value.instrument_id.as_str() == underlying_id)
                {
                    return Err(ReferenceError::Invalid(format!(
                        "instrument {} references missing underlying instrument {}",
                        instrument.instrument_id, underlying_id
                    )));
                }
            }
        }

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
        let market_ids: std::collections::BTreeSet<_> = self
            .markets
            .iter()
            .map(|value| value.market_id.as_str())
            .collect();
        let mut market_by_listing = std::collections::BTreeSet::new();
        for listing in &self.listings {
            required(
                listing.exchange_symbol.as_str(),
                &format!("listing {} exchange symbol", listing.listing_id),
            )?;
            required(
                listing.status.as_str(),
                &format!("listing {} status", listing.listing_id),
            )?;
            interval(
                listing.effective_from_unix_nanos,
                listing.effective_to_unix_nanos,
                &format!("listing {}", listing.listing_id),
            )?;
            if !instrument_ids.contains(listing.instrument_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "listing {} references missing instrument {}",
                    listing.listing_id, listing.instrument_id
                )));
            }
            if !entity_ids.contains(listing.exchange_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "listing {} references missing exchange {}",
                    listing.listing_id, listing.exchange_id
                )));
            }
        }
        for market in &self.markets {
            required(
                &market.market_key,
                &format!("market {} key", market.market_id),
            )?;
            required(
                &market.market_type,
                &format!("market {} type", market.market_id),
            )?;
            required(
                market.source_symbol.as_str(),
                &format!("market {} source symbol", market.market_id),
            )?;
            required(
                market.status.as_str(),
                &format!("market {} status", market.market_id),
            )?;
            if market.price_precision < 0 || market.quantity_precision < 0 {
                return Err(ReferenceError::Invalid(format!(
                    "market {} precision must not be negative",
                    market.market_id
                )));
            }
            non_negative_decimal(
                market.price_tick.as_deref(),
                &format!("market {} price tick", market.market_id),
            )?;
            non_negative_decimal(
                market.quantity_tick.as_deref(),
                &format!("market {} quantity tick", market.market_id),
            )?;
            non_negative_decimal(
                market.minimum_quantity.as_deref(),
                &format!("market {} minimum quantity", market.market_id),
            )?;
            non_negative_decimal(
                market.minimum_notional.as_deref(),
                &format!("market {} minimum notional", market.market_id),
            )?;
            non_negative_decimal(
                market.contract_size.as_deref(),
                &format!("market {} contract size", market.market_id),
            )?;
            interval(
                market.effective_from_unix_nanos,
                market.effective_to_unix_nanos,
                &format!("market {}", market.market_id),
            )?;
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
            let Some(listing) = self
                .listings
                .iter()
                .find(|value| value.listing_id == market.listing_id)
            else {
                return Err(ReferenceError::Invalid(format!(
                    "market {} references missing listing {}",
                    market.market_id, market.listing_id
                )));
            };
            if listing.instrument_id != market.instrument_id {
                return Err(ReferenceError::Invalid(format!(
                    "market {} instrument {} disagrees with listing {} instrument {}",
                    market.market_id,
                    market.instrument_id,
                    market.listing_id,
                    listing.instrument_id
                )));
            }
            if listing.exchange_id != market.exchange_id {
                return Err(ReferenceError::Invalid(format!(
                    "market {} exchange {} disagrees with listing {} exchange {}",
                    market.market_id, market.exchange_id, market.listing_id, listing.exchange_id
                )));
            }
            if !market_by_listing.insert(market.listing_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "listing {} is associated with more than one market",
                    market.listing_id
                )));
            }
            if !entity_ids.contains(market.exchange_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "market {} references missing exchange {}",
                    market.market_id, market.exchange_id
                )));
            }
            if let Some(underlying_id) = market.underlying_instrument_id.as_deref() {
                if !instrument_ids.contains(underlying_id) {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} references missing underlying instrument {}",
                        market.market_id, underlying_id
                    )));
                }
            }
            if let Some(asset_id) = market.base_asset_id.as_deref() {
                if !asset_ids.contains(asset_id) {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} references missing base asset {}",
                        market.market_id, asset_id
                    )));
                }
            }
            if let Some(asset_id) = market.quote_asset_id.as_deref() {
                if !asset_ids.contains(asset_id) {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} references missing quote asset {}",
                        market.market_id, asset_id
                    )));
                }
            }
        }
        for access in &self.execution_accesses {
            required(
                &access.provider_id,
                &format!("execution access {} provider", access.access_id),
            )?;
            required(
                &access.product_family,
                &format!("execution access {} product family", access.access_id),
            )?;
            required(
                &access.provider_symbol,
                &format!("execution access {} provider symbol", access.access_id),
            )?;
            required(
                access.status.as_str(),
                &format!("execution access {} status", access.access_id),
            )?;
            if !market_ids.contains(access.market_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "execution access {} references missing market {}",
                    access.access_id, access.market_id
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
            required(
                &product.product_type,
                &format!("financial product {} type", product.product_id),
            )?;
            required(
                &product.name,
                &format!("financial product {} name", product.product_id),
            )?;
            required(
                &product.provider_product_id,
                &format!(
                    "financial product {} provider product id",
                    product.product_id
                ),
            )?;
            required(
                product.status.as_str(),
                &format!("financial product {} status", product.product_id),
            )?;
            if product.lock_period_days < 0 {
                return Err(ReferenceError::Invalid(format!(
                    "financial product {} lock period must not be negative",
                    product.product_id
                )));
            }
            non_negative_decimal(
                product.min_amount.as_deref(),
                &format!("financial product {} min amount", product.product_id),
            )?;
            non_negative_decimal(
                product.max_amount.as_deref(),
                &format!("financial product {} max amount", product.product_id),
            )?;
            non_negative_decimal(
                product.apr.as_deref(),
                &format!("financial product {} apr", product.product_id),
            )?;
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
            if let Some(issuer_id) = product.issuer_id.as_deref() {
                if !entity_ids.contains(issuer_id) {
                    return Err(ReferenceError::Invalid(format!(
                        "financial product {} references missing issuer {}",
                        product.product_id, issuer_id
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
