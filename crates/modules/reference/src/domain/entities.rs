//! Reference domain entities and provider snapshots.

use kairos_primitives::decimal::{Money, Price, Quantity};
use kairos_primitives::reference::{
    AssetClass, AssetId, Exchange, InstrumentId, InstrumentKind, IssuerId, ListingId, MarketId,
    ReferenceStatus, Symbol,
};
use kairos_primitives::time::{Generation, UnixNanos};
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
    pub code: Symbol,
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
    pub strike: Option<Price>,
    pub option_right: Option<String>,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Listing {
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
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    #[serde(default)]
    pub listing_id: Option<ListingId>,
    pub exchange_id: Exchange,
    pub instrument_kind: InstrumentKind,
    #[serde(default)]
    pub asset_type: Option<AssetClass>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    #[serde(default)]
    pub venue_symbol: Option<Symbol>,
    pub base_asset_id: Option<AssetId>,
    pub quote_asset_id: Option<AssetId>,
    pub status: ReferenceStatus,
    pub price_tick: Option<Price>,
    pub quantity_tick: Option<Quantity>,
    pub price_precision: i32,
    pub quantity_precision: i32,
    pub minimum_quantity: Option<Quantity>,
    pub minimum_notional: Option<Money>,
    pub contract_size: Option<Quantity>,
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
    pub venue_symbol: Option<Symbol>,
    pub previous_status: Option<ReferenceStatus>,
    pub current_status: Option<ReferenceStatus>,
    pub previous_symbol: Option<String>,
    pub current_symbol: Option<String>,
    #[serde(default)]
    pub operation: Option<String>,
    #[serde(default)]
    pub generation: Generation,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProviderCatalog {
    pub entities: Vec<Entity>,
    pub assets: Vec<Asset>,
    pub instruments: Vec<Instrument>,
    pub listings: Vec<Listing>,
    pub markets: Vec<Market>,
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
            for value in &catalog.assets {
                if let Some(previous) = assets.get_mut(&value.asset_id) {
                    merge_asset(previous, value).map_err(|reason| {
                        ReferenceError::Invalid(format!(
                            "canonical asset conflict for {}: {reason}",
                            value.asset_id
                        ))
                    })?;
                } else {
                    assets.insert(value.asset_id.clone(), value.clone());
                }
            }
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
            if market.instrument_kind == InstrumentKind::Unknown {
                return Err(ReferenceError::Invalid(format!(
                    "market {} instrument kind is unknown",
                    market.market_id
                )));
            }
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
            if market
                .minimum_notional
                .is_some_and(|value| value.mantissa() < 0)
            {
                return Err(ReferenceError::Invalid(format!(
                    "market {} minimum notional must not be negative",
                    market.market_id
                )));
            }
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
            let instrument = self
                .instruments
                .iter()
                .find(|instrument| instrument.instrument_id == market.instrument_id)
                .expect("instrument membership checked above");
            if instrument.instrument_type != market.instrument_kind {
                return Err(ReferenceError::Invalid(format!(
                    "market {} kind {:?} does not match instrument {} kind {:?}",
                    market.market_id,
                    market.instrument_kind,
                    instrument.instrument_id,
                    instrument.instrument_type
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
    let status = merged_reference_status(previous.status, incoming.status);
    let mut left = previous.clone();
    let mut right = incoming.clone();
    left.source_id = None;
    right.source_id = None;
    left.status = status;
    right.status = status;
    let mut fields = Vec::new();
    macro_rules! merge_optional {
        ($field:ident) => {
            match (&left.$field, &right.$field) {
                (None, Some(value)) => left.$field = Some(value.clone()),
                (Some(value), None) => right.$field = Some(value.clone()),
                (Some(left_value), Some(right_value)) if left_value != right_value => {
                    fields.push(stringify!($field));
                },
                _ => {},
            }
        };
    }
    merge_optional!(name);
    merge_optional!(issuer_id);
    merge_optional!(share_class);
    merge_optional!(primary_currency_asset_id);
    merge_optional!(underlying_instrument_id);
    merge_optional!(expiry_unix_nanos);
    merge_optional!(strike);
    merge_optional!(option_right);
    if left != right {
        if left.symbol != right.symbol {
            fields.push("symbol");
        }
        if left.instrument_type != right.instrument_type {
            fields.push("instrument_type");
        }
        if fields.is_empty() {
            fields.push("canonical attributes");
        }
        return Err(format!("different {}", fields.join(", ")));
    }
    *previous = left;
    Ok(())
}

pub(crate) fn merge_asset(previous: &mut Asset, incoming: &Asset) -> Result<(), String> {
    let status = merged_reference_status(previous.status, incoming.status);
    let mut left = previous.clone();
    let mut right = incoming.clone();
    left.source_id = None;
    right.source_id = None;
    left.status = status;
    right.status = status;
    match (&left.name, &right.name) {
        (None, Some(value)) => left.name = Some(value.clone()),
        (Some(value), None) => right.name = Some(value.clone()),
        _ => {},
    }
    if left != right {
        let mut fields = Vec::new();
        if left.code != right.code {
            fields.push("code");
        }
        if left.asset_class != right.asset_class {
            fields.push("asset_class");
        }
        if left.name != right.name {
            fields.push("name");
        }
        if fields.is_empty() {
            fields.push("canonical attributes");
        }
        return Err(format!("different {}", fields.join(", ")));
    }
    *previous = left;
    Ok(())
}

fn merged_reference_status(
    left: kairos_primitives::reference::ReferenceStatus,
    right: kairos_primitives::reference::ReferenceStatus,
) -> kairos_primitives::reference::ReferenceStatus {
    use kairos_primitives::reference::ReferenceStatus;
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
