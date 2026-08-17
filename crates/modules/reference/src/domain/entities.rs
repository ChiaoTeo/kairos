//! Reference domain entities and provider snapshots.

use kairos_primitives::{
    AssetClass, AssetId, Exchange, ExecutionAccessId, Generation, InstrumentId, InstrumentKind,
    IssuerId, ListingId, MarketId, ProviderId, ProviderProductCode, ProviderSymbol, Rate,
    ReferenceStatus, Symbol, UnixNanos,
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
    pub entity_type: EntityKind,
    pub name: String,
    pub status: ReferenceStatus,
}

#[derive(
    Clone, Copy, Debug, Default, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum EntityKind {
    Exchange,
    Broker,
    DataProvider,
    Issuer,
    #[default]
    Unknown,
}

impl EntityKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Exchange => "exchange",
            Self::Broker => "broker",
            Self::DataProvider => "data_provider",
            Self::Issuer => "issuer",
            Self::Unknown => "unknown",
        }
    }
}

impl From<&str> for EntityKind {
    fn from(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "exchange" => Self::Exchange,
            "broker" => Self::Broker,
            "data_provider" | "provider" => Self::DataProvider,
            "issuer" => Self::Issuer,
            _ => Self::Unknown,
        }
    }
}

impl From<String> for EntityKind {
    fn from(value: String) -> Self {
        Self::from(value.as_str())
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Asset {
    #[serde(default)]
    pub source_id: Option<String>,
    pub asset_id: AssetId,
    pub code: String,
    pub name: Option<String>,
    pub asset_class: AssetClass,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Instrument {
    #[serde(default)]
    pub source_id: Option<String>,
    pub instrument_id: InstrumentId,
    pub symbol: Symbol,
    pub name: Option<String>,
    pub instrument_type: InstrumentKind,
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
    #[serde(default)]
    pub listing_id: Option<ListingId>,
    pub exchange_id: Exchange,
    /// Provider/venue product surface. The legacy field name is retained only
    /// for persisted/wire compatibility; this is not a canonical market kind.
    pub market_type: ProviderProductCode,
    #[serde(default)]
    pub asset_type: Option<AssetClass>,
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
    pub generation: Generation,
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
    #[serde(default)]
    pub instrument_id: Option<InstrumentId>,
    #[serde(default)]
    pub listing_id: Option<ListingId>,
    #[serde(default)]
    pub market_id: Option<MarketId>,
    pub provider_id: ProviderId,
    /// Provider-owned request discriminator, not a canonical product family.
    #[serde(rename = "product_family")]
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
    pub settlement_asset_id: Option<AssetId>,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

/// Provider-specific market-data address for a canonical Market.
///
/// This is deliberately separate from ExecutionAccess: the provider and
/// symbol used for observations do not imply the provider and symbol used for
/// order entry.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketDataAccess {
    #[serde(default)]
    pub source_id: Option<String>,
    pub access_id: String,
    pub market_id: MarketId,
    pub provider_id: ProviderId,
    /// Provider-owned request discriminator, not a canonical product family.
    #[serde(rename = "product_family")]
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

impl Default for MarketDataAccess {
    fn default() -> Self {
        Self {
            source_id: None,
            access_id: "market-data-access:default".into(),
            market_id: MarketId::new("market:default").expect("valid market ID"),
            provider_id: ProviderId::new("provider:unknown").expect("valid default provider"),
            provider_product: ProviderProductCode::new("unknown")
                .expect("valid default provider product"),
            provider_symbol: ProviderSymbol::new("symbol:default").expect("valid provider symbol"),
            status: ReferenceStatus::Unknown,
            effective_from_unix_nanos: UnixNanos::default(),
            effective_to_unix_nanos: None,
        }
    }
}

impl Default for ExecutionAccess {
    fn default() -> Self {
        Self {
            source_id: None,
            access_id: ExecutionAccessId::new("access:default").expect("valid access ID"),
            instrument_id: None,
            listing_id: None,
            market_id: Some(MarketId::new("market:default").expect("valid market ID")),
            provider_id: ProviderId::new("provider:unknown").expect("valid default provider"),
            provider_product: ProviderProductCode::new("unknown")
                .expect("valid default provider product"),
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
    pub execution_accesses: Vec<ExecutionAccess>,
    #[serde(default)]
    pub market_data_accesses: Vec<MarketDataAccess>,
}

impl ProviderCatalog {
    /// Merge independently authoritative provider projections into one
    /// canonical candidate. This is a Reference domain rule: persistence and
    /// provider composition must not each invent their own conflict policy.
    pub fn merge<'a>(
        catalogs: impl IntoIterator<Item = &'a ProviderCatalog>,
    ) -> ReferenceResult<Self> {
        let mut entities = std::collections::BTreeMap::new();
        let mut assets = std::collections::BTreeMap::new();
        let mut instruments = std::collections::BTreeMap::new();
        let mut listings = std::collections::BTreeMap::new();
        let mut markets = std::collections::BTreeMap::new();
        let mut execution_accesses = std::collections::BTreeMap::new();
        let mut market_data_accesses = std::collections::BTreeMap::new();
        let mut conflicts = Vec::new();

        macro_rules! merge_exact {
            ($catalog:expr, $field:ident, $key:expr, $label:literal) => {
                for value in &$catalog.$field {
                    let key = $key(value);
                    if $field
                        .insert(key.clone(), value.clone())
                        .is_some_and(|previous| previous != *value)
                    {
                        conflicts.push(format!(
                            concat!("irreconcilable canonical ", $label, " conflict for {}"),
                            key
                        ));
                    }
                }
            };
        }

        for catalog in catalogs {
            merge_exact!(
                catalog,
                entities,
                |value: &Entity| value.entity_id.clone(),
                "entity"
            );
            merge_exact!(
                catalog,
                assets,
                |value: &Asset| value.asset_id.clone(),
                "asset"
            );
            for value in &catalog.instruments {
                if let Some(previous) = instruments.get_mut(&value.instrument_id) {
                    if previous != value {
                        merge_instrument(previous, value).map_err(|reason| {
                            ReferenceError::Invalid(format!(
                                "canonical instrument conflict for {}: {reason}",
                                value.instrument_id
                            ))
                        })?;
                    }
                } else {
                    instruments.insert(value.instrument_id.clone(), value.clone());
                }
            }
            merge_exact!(
                catalog,
                listings,
                |value: &Listing| value.listing_id.clone(),
                "listing"
            );
            merge_exact!(
                catalog,
                markets,
                |value: &Market| value.market_id.clone(),
                "market"
            );
            merge_exact!(
                catalog,
                execution_accesses,
                |value: &ExecutionAccess| value.access_id.clone(),
                "execution_access"
            );
            merge_exact!(
                catalog,
                market_data_accesses,
                |value: &MarketDataAccess| value.access_id.clone(),
                "market_data_access"
            );
        }

        if !conflicts.is_empty() {
            let sample = conflicts.iter().take(8).cloned().collect::<Vec<_>>();
            return Err(ReferenceError::Invalid(format!(
                "providers returned {} canonical record conflicts (sample: {})",
                conflicts.len(),
                sample.join(", ")
            )));
        }
        let candidate = Self {
            entities: entities.into_values().collect(),
            assets: assets.into_values().collect(),
            instruments: instruments.into_values().collect(),
            listings: listings.into_values().collect(),
            markets: markets.into_values().collect(),
            execution_accesses: execution_accesses.into_values().collect(),
            market_data_accesses: market_data_accesses.into_values().collect(),
        };
        Ok(candidate)
    }

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
                    return Err(ReferenceError::DuplicateId {
                        record_kind: label.to_owned(),
                        record_id: id.to_owned(),
                    });
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
        unique(&self.execution_accesses, "execution access", |value| {
            &value.access_id
        })?;
        unique(&self.market_data_accesses, "market data access", |value| {
            &value.access_id
        })?;

        for entity in &self.entities {
            if entity.entity_type == EntityKind::Unknown {
                return Err(ReferenceError::Invalid(format!(
                    "entity {} has unknown kind",
                    entity.entity_id
                )));
            }
            required(&entity.name, &format!("entity {} name", entity.entity_id))?;
            required(
                entity.status.as_str(),
                &format!("entity {} status", entity.entity_id),
            )?;
        }
        for asset in &self.assets {
            required(&asset.code, &format!("asset {} code", asset.asset_id))?;
            if asset.asset_class == AssetClass::Unknown {
                return Err(ReferenceError::Invalid(format!(
                    "asset {} has unknown canonical class",
                    asset.asset_id
                )));
            }
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
            if instrument.instrument_type == InstrumentKind::Unknown {
                return Err(ReferenceError::Invalid(format!(
                    "instrument {} has unknown canonical kind",
                    instrument.instrument_id
                )));
            }
            required(
                instrument.status.as_str(),
                &format!("instrument {} status", instrument.instrument_id),
            )?;
            non_negative_decimal(
                instrument.strike.as_deref(),
                &format!("instrument {} strike", instrument.instrument_id),
            )?;
            if instrument.instrument_type == InstrumentKind::Option
                && (instrument.expiry_unix_nanos.is_none()
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
            if instrument.instrument_type == InstrumentKind::Future
                && instrument.expiry_unix_nanos.is_none()
            {
                return Err(ReferenceError::Invalid(format!(
                    "future instrument {} requires expiry",
                    instrument.instrument_id
                )));
            }
            if instrument.instrument_type == InstrumentKind::Perpetual
                && instrument.expiry_unix_nanos.is_some()
            {
                return Err(ReferenceError::Invalid(format!(
                    "perpetual instrument {} must not have expiry",
                    instrument.instrument_id
                )));
            }
            if instrument.instrument_type != InstrumentKind::Option
                && (instrument.strike.is_some() || instrument.option_right.is_some())
            {
                return Err(ReferenceError::Invalid(format!(
                    "non-option instrument {} must not have option terms",
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
            if market.asset_type == Some(AssetClass::Unknown) {
                return Err(ReferenceError::Invalid(format!(
                    "market {} has unknown asset class",
                    market.market_id
                )));
            }
            required(
                &market.market_key,
                &format!("market {} key", market.market_id),
            )?;
            required(
                market.market_type.as_str(),
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
            if let Some(listing_id) = market.listing_id.as_ref() {
                if !listing_ids.contains(listing_id.as_str()) {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} references missing listing {}",
                        market.market_id, listing_id
                    )));
                }
                let listing = self
                    .listings
                    .iter()
                    .find(|value| &value.listing_id == listing_id)
                    .expect("validated listing id must resolve");
                if listing.instrument_id != market.instrument_id {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} instrument {} disagrees with listing {} instrument {}",
                        market.market_id, market.instrument_id, listing_id, listing.instrument_id
                    )));
                }
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
                access.provider_id.as_str(),
                &format!("execution access {} provider", access.access_id),
            )?;
            required(
                access.provider_product.as_str(),
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
            if access.instrument_id.is_none() && access.market_id.is_none() {
                return Err(ReferenceError::Invalid(format!(
                    "execution access {} requires an instrument or market",
                    access.access_id
                )));
            }
            if let Some(instrument_id) = access.instrument_id.as_ref() {
                if !instrument_ids.contains(instrument_id.as_str()) {
                    return Err(ReferenceError::Invalid(format!(
                        "execution access {} references missing instrument {}",
                        access.access_id, instrument_id
                    )));
                }
            }
            if let Some(listing_id) = access.listing_id.as_ref() {
                if !listing_ids.contains(listing_id.as_str()) {
                    return Err(ReferenceError::Invalid(format!(
                        "execution access {} references missing listing {}",
                        access.access_id, listing_id
                    )));
                }
            }
            if let Some(market_id) = access.market_id.as_ref() {
                if !market_ids.contains(market_id.as_str()) {
                    return Err(ReferenceError::Invalid(format!(
                        "execution access {} references missing market {}",
                        access.access_id, market_id
                    )));
                }
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
        for access in &self.market_data_accesses {
            required(
                access.provider_id.as_str(),
                &format!("market data access {} provider", access.access_id),
            )?;
            required(
                access.provider_product.as_str(),
                &format!("market data access {} product family", access.access_id),
            )?;
            required(
                &access.provider_symbol,
                &format!("market data access {} provider symbol", access.access_id),
            )?;
            required(
                access.status.as_str(),
                &format!("market data access {} status", access.access_id),
            )?;
            if !market_ids.contains(access.market_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "market data access {} references missing market {}",
                    access.access_id, access.market_id
                )));
            }
            if access
                .effective_to_unix_nanos
                .is_some_and(|end| end <= access.effective_from_unix_nanos)
            {
                return Err(ReferenceError::Invalid(format!(
                    "market data access {} has an invalid effective interval",
                    access.access_id
                )));
            }
        }
        Ok(())
    }
}

pub(crate) fn reconcile_instruments(values: &mut Vec<Instrument>) -> ReferenceResult<()> {
    let mut reconciled = std::collections::BTreeMap::new();
    for value in std::mem::take(values) {
        if let Some(previous) = reconciled.get_mut(&value.instrument_id) {
            merge_instrument(previous, &value).map_err(|reason| {
                ReferenceError::Invalid(format!(
                    "provider produced conflicting canonical instrument {}: {reason}",
                    value.instrument_id
                ))
            })?;
        } else {
            reconciled.insert(value.instrument_id.clone(), value);
        }
    }
    *values = reconciled.into_values().collect();
    Ok(())
}

pub(crate) fn merge_instrument(
    previous: &mut Instrument,
    incoming: &Instrument,
) -> Result<(), String> {
    let status = merged_instrument_status(previous.status, incoming.status);
    let mut left = previous.clone();
    let mut right = incoming.clone();
    left.source_id = None;
    right.source_id = None;
    left.status = status;
    right.status = status;
    if left != right {
        let mut fields = Vec::new();
        if left.symbol != right.symbol {
            fields.push("symbol");
        }
        if left.instrument_type != right.instrument_type {
            fields.push("instrument_type");
        }
        if left.primary_currency_asset_id != right.primary_currency_asset_id {
            fields.push("primary_currency_asset_id");
        }
        if left.underlying_instrument_id != right.underlying_instrument_id {
            fields.push("underlying_instrument_id");
        }
        if left.expiry_unix_nanos != right.expiry_unix_nanos {
            fields.push("expiry_unix_nanos");
        }
        if left.strike != right.strike {
            fields.push("strike");
        }
        if left.option_right != right.option_right {
            fields.push("option_right");
        }
        if fields.is_empty() {
            fields.push("canonical attributes");
        }
        return Err(format!("different {}", fields.join(", ")));
    }
    *previous = left;
    Ok(())
}

fn merged_instrument_status(
    left: kairos_primitives::ReferenceStatus,
    right: kairos_primitives::ReferenceStatus,
) -> kairos_primitives::ReferenceStatus {
    use kairos_primitives::ReferenceStatus;
    if matches!(left, ReferenceStatus::Active | ReferenceStatus::Trading)
        || matches!(right, ReferenceStatus::Active | ReferenceStatus::Trading)
    {
        ReferenceStatus::Active
    } else if left == right {
        left
    } else if left == ReferenceStatus::Unknown {
        right
    } else if right == ReferenceStatus::Unknown {
        left
    } else {
        ReferenceStatus::Inactive
    }
}
