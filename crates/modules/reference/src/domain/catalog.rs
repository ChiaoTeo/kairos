//! Reference catalog aggregate and lifecycle reconciliation.

use std::collections::{BTreeMap, BTreeSet};

use kairos_primitives::reference::{
    ExchangeId, InstrumentId, ListingId, MarketId, ReferenceSourceId, ReferenceStatus, VenueId,
};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use super::{
    Asset, Exchange, Instrument, LifecycleEvent, Listing, Market, ProviderCatalog,
    ProviderCatalogMembership, ReferenceError, ReferenceResult, Venue, VenueIdentifierMapping,
    VenueListing, VenueMarket,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ManualUpsertPolicy {
    pub provenance: String,
    pub conflict_policy: String,
    pub reject_provider_owned: bool,
}

impl ManualUpsertPolicy {
    pub fn new(
        provenance: impl Into<String>,
        conflict_policy: impl Into<String>,
        reject_provider_owned: bool,
    ) -> Self {
        Self {
            provenance: provenance.into(),
            conflict_policy: conflict_policy.into(),
            reject_provider_owned,
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceCatalog {
    #[serde(default)]
    pub venues: BTreeMap<VenueId, Venue>,
    pub exchanges: BTreeMap<ExchangeId, Exchange>,
    pub assets: BTreeMap<String, Asset>,
    pub instruments: BTreeMap<InstrumentId, Instrument>,
    pub listings: BTreeMap<ListingId, Listing>,
    pub markets: BTreeMap<MarketId, Market>,
    #[serde(default)]
    pub venue_listings: BTreeMap<ListingId, VenueListing>,
    #[serde(default)]
    pub venue_markets: BTreeMap<MarketId, VenueMarket>,
    #[serde(default)]
    pub provider_catalog_memberships:
        BTreeMap<(ReferenceSourceId, InstrumentId), ProviderCatalogMembership>,
    #[serde(default)]
    pub venue_identifier_mappings:
        BTreeMap<(String, String, String, String), VenueIdentifierMapping>,
    pub lifecycle_events: Vec<LifecycleEvent>,
    pub generation: Generation,
    pub event_sequence: Sequence,
}

/// Reconciliation changes retain record identities, not a full optional-field
/// lifecycle payload for every ordinary record. Expansion preserves wire/audit
/// events and is performed one event at a time by the existing consumers.
pub struct CatalogEventBatch {
    entries: Vec<CatalogChange>,
    observed_at: UnixNanos,
    previous_sequence: Sequence,
    generation: Generation,
}

enum CatalogChange {
    Record {
        kind: &'static str,
        event_type: &'static str,
        id: String,
    },
    Detailed(Box<LifecycleEvent>),
}

impl CatalogEventBatch {
    fn new(observed_at: UnixNanos, previous_sequence: Sequence) -> Self {
        Self {
            entries: Vec::new(),
            observed_at,
            previous_sequence,
            generation: Generation::default(),
        }
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    fn push_record(&mut self, kind: &'static str, event_type: &'static str, id: &str) {
        self.entries.push(CatalogChange::Record {
            kind,
            event_type,
            id: id.to_owned(),
        });
    }

    pub(crate) fn push(&mut self, event: LifecycleEvent) {
        self.entries.push(CatalogChange::Detailed(Box::new(event)));
    }

    fn finish(&mut self, generation: Generation) {
        self.generation = generation;
        for change in &mut self.entries {
            if let CatalogChange::Detailed(event) = change {
                event.generation = generation;
                event.operation = Some("upsert".into());
            }
        }
    }

    pub fn iter(
        &self,
    ) -> impl ExactSizeIterator<Item = LifecycleEvent> + DoubleEndedIterator + Clone + '_ {
        self.entries
            .iter()
            .enumerate()
            .map(|(offset, change)| match change {
                CatalogChange::Record {
                    kind,
                    event_type,
                    id,
                } => {
                    let mut event = record_event(
                        kind,
                        event_type,
                        id,
                        self.observed_at,
                        self.previous_sequence.get() + offset as u64 + 1,
                    );
                    event.generation = self.generation;
                    event.operation = Some("upsert".into());
                    event
                },
                CatalogChange::Detailed(event) => (**event).clone(),
            })
    }
}

impl ReferenceCatalog {
    pub(crate) const LIFECYCLE_HISTORY_LIMIT: usize = 4096;

    #[cfg(test)]
    pub fn apply(&mut self, incoming: ProviderCatalog, now: UnixNanos) -> Vec<LifecycleEvent> {
        self.apply_events(incoming, now).iter().collect()
    }

    pub fn apply_events(
        &mut self,
        mut incoming: ProviderCatalog,
        now: UnixNanos,
    ) -> CatalogEventBatch {
        for exchange in &mut incoming.exchanges {
            exchange.normalize_canonical_name();
        }
        materialize_v3_compatibility(&mut incoming);
        let previous_venues = std::mem::take(&mut self.venues);
        let previous_exchanges = std::mem::take(&mut self.exchanges);
        let previous_assets = std::mem::take(&mut self.assets);
        let previous_instruments = std::mem::take(&mut self.instruments);
        let previous_listings = std::mem::take(&mut self.listings);
        let previous_markets = std::mem::take(&mut self.markets);
        let previous_venue_listings = std::mem::take(&mut self.venue_listings);
        let previous_venue_markets = std::mem::take(&mut self.venue_markets);
        let previous_memberships = std::mem::take(&mut self.provider_catalog_memberships);
        let previous_venue_identifier_mappings =
            std::mem::take(&mut self.venue_identifier_mappings);
        self.venues = incoming
            .venues
            .into_iter()
            .map(|value| (value.venue_id.clone(), value))
            .collect();
        self.exchanges = incoming
            .exchanges
            .into_iter()
            .map(|v| (v.exchange_id.clone(), v))
            .collect();
        self.assets = incoming
            .assets
            .into_iter()
            .map(|v| (v.asset_id.to_string(), v))
            .collect();
        self.instruments = incoming
            .instruments
            .into_iter()
            .map(|v| (v.instrument_id.clone(), v))
            .collect();
        self.listings = incoming
            .listings
            .into_iter()
            .map(|v| (v.listing_id.clone(), v))
            .collect();
        self.venue_listings = incoming
            .venue_listings
            .into_iter()
            .map(|value| (value.listing_id.clone(), value))
            .collect();
        self.venue_markets = incoming
            .venue_markets
            .into_iter()
            .map(|value| (value.market_id.clone(), value))
            .collect();
        self.provider_catalog_memberships = if incoming.provider_catalog_memberships.is_empty() {
            // Normalized provider storage derives catalog membership from its
            // committed instrument facts in the same transaction. An empty
            // overlay therefore means "no explicit membership assertion",
            // not "retire every persisted membership".
            previous_memberships.clone()
        } else {
            incoming
                .provider_catalog_memberships
                .into_iter()
                .map(|value| {
                    (
                        (value.source_id.clone(), value.instrument_id.clone()),
                        value,
                    )
                })
                .collect()
        };
        self.venue_identifier_mappings = incoming
            .venue_identifier_mappings
            .into_iter()
            .map(|value| {
                (
                    (
                        value.provider.to_string(),
                        value.provider_product.clone(),
                        value.identifier_kind.as_str().to_owned(),
                        value.identifier.clone(),
                    ),
                    value,
                )
            })
            .collect();

        // Canonical identity is retained after a provider withdrawal. A
        // missing provider fact is expressed as an effective lifecycle
        // transition, never as a hard delete that makes historical identity
        // or an already-committed event impossible to resolve.
        for (id, previous) in &previous_venues {
            self.venues.entry(id.clone()).or_insert_with(|| {
                let mut retained = previous.clone();
                if retained.status != ReferenceStatus::Delisted {
                    retained.status = ReferenceStatus::Inactive;
                }
                retained
            });
        }
        for (id, previous) in &previous_venue_listings {
            self.venue_listings.entry(id.clone()).or_insert_with(|| {
                let mut retained = previous.clone();
                if retained.status != ReferenceStatus::Delisted {
                    retained.status = ReferenceStatus::Inactive;
                }
                retained.effective_to_unix_nanos.get_or_insert(now);
                retained
            });
        }
        for (id, previous) in &previous_venue_markets {
            self.venue_markets.entry(id.clone()).or_insert_with(|| {
                let mut retained = previous.clone();
                if retained.status != ReferenceStatus::Delisted {
                    retained.status = ReferenceStatus::Inactive;
                }
                retained.effective_to_unix_nanos.get_or_insert(now);
                retained
            });
        }
        for (id, previous) in &previous_exchanges {
            self.exchanges.entry(id.clone()).or_insert_with(|| {
                let mut retained = previous.clone();
                retained.status = ReferenceStatus::Inactive;
                retained
            });
        }
        for (id, previous) in &previous_assets {
            self.assets.entry(id.clone()).or_insert_with(|| {
                let mut retained = previous.clone();
                retained.status = ReferenceStatus::Inactive;
                retained
            });
        }
        for (id, previous) in &previous_instruments {
            self.instruments.entry(id.clone()).or_insert_with(|| {
                let mut retained = previous.clone();
                retained.status = ReferenceStatus::Inactive;
                retained
            });
        }
        for (id, previous) in &previous_listings {
            self.listings.entry(id.clone()).or_insert_with(|| {
                let mut retained = previous.clone();
                retained.status = ReferenceStatus::Inactive;
                retained.effective_to_unix_nanos.get_or_insert(now);
                retained
            });
        }

        let mut next_markets: BTreeMap<_, _> = incoming
            .markets
            .into_iter()
            .map(|v| (v.market_id.clone(), v))
            .collect();
        let mut events = CatalogEventBatch::new(now, self.event_sequence);

        macro_rules! diff_records {
            ($kind:literal, $previous:expr, $current:expr) => {
                for (id, next) in &$current {
                    let event_type = match $previous.get(id) {
                        None => Some(concat!($kind, "_added")),
                        Some(previous) if previous != next => Some(concat!($kind, "_changed")),
                        _ => None,
                    };
                    if let Some(event_type) = event_type {
                        events.push_record($kind, event_type, id);
                    }
                }
            };
        }

        diff_records!("exchange", previous_exchanges, self.exchanges);
        diff_records!("venue", previous_venues, self.venues);
        diff_records!("asset", previous_assets, self.assets);
        diff_records!("instrument", previous_instruments, self.instruments);
        diff_records!("listing", previous_listings, self.listings);
        diff_records!(
            "venue_listing",
            previous_venue_listings,
            self.venue_listings
        );
        diff_records!("venue_market", previous_venue_markets, self.venue_markets);
        for ((source_id, instrument_id), next) in &self.provider_catalog_memberships {
            let key = (source_id.clone(), instrument_id.clone());
            let event_type = match previous_memberships.get(&key) {
                None => Some("provider_catalog_membership_added"),
                Some(previous) if previous != next => Some("provider_catalog_membership_changed"),
                _ => None,
            };
            if let Some(event_type) = event_type {
                events.push_record(
                    "provider_catalog_membership",
                    event_type,
                    &format!("{source_id}|{instrument_id}"),
                );
            }
        }
        if previous_venue_identifier_mappings != self.venue_identifier_mappings {
            let affected_venues = previous_venue_identifier_mappings
                .values()
                .chain(self.venue_identifier_mappings.values())
                .map(|value| value.venue_id.clone())
                .collect::<BTreeSet<_>>();
            for venue_id in affected_venues {
                if self.venues.contains_key(&venue_id) {
                    events.push_record("venue", "venue_changed", venue_id.as_str());
                }
            }
        }

        for (id, next) in &next_markets {
            match previous_markets.get(id) {
                None => events.push(LifecycleEvent::listed(
                    next,
                    now,
                    self.event_sequence.get() + events.len() as u64 + 1,
                )),
                Some(previous) if previous != next => {
                    let event_type = if previous.venue_symbol != next.venue_symbol {
                        "symbol_changed"
                    } else if previous.status != next.status {
                        "status_changed"
                    } else {
                        "market_changed"
                    };
                    events.push(LifecycleEvent {
                        event_id: format!(
                            "reference:{:020}",
                            self.event_sequence.get() + events.len() as u64 + 1
                        ),
                        event_type: event_type.to_string(),
                        event_time_unix_nanos: now,
                        record_kind: Some("market".to_string()),
                        record_id: Some(id.to_string()),
                        market_id: Some(id.clone()),
                        instrument_id: Some(next.instrument_id.clone()),
                        listing_id: next.listing_id.clone(),
                        exchange_id: Some(next.exchange_id.clone()),
                        venue_symbol: next.venue_symbol.clone(),
                        previous_status: Some(previous.status),
                        current_status: Some(next.status),
                        previous_symbol: previous.venue_symbol.as_ref().map(ToString::to_string),
                        current_symbol: next.venue_symbol.as_ref().map(ToString::to_string),
                        ..LifecycleEvent::default()
                    });
                },
                _ => {},
            }
        }
        let mut delisted_records = Vec::new();
        for (id, previous) in &previous_markets {
            if !next_markets.contains_key(id) && previous.status == ReferenceStatus::Delisted {
                delisted_records.push((id.clone(), previous.clone()));
                continue;
            }
            if !next_markets.contains_key(id) && previous.status != ReferenceStatus::Delisted {
                let mut delisted = previous.clone();
                delisted.status = ReferenceStatus::Delisted;
                delisted.effective_to_unix_nanos = Some(now);
                events.push(LifecycleEvent {
                    event_id: format!(
                        "reference:{:020}",
                        self.event_sequence.get() + events.len() as u64 + 1
                    ),
                    event_type: "delisted".to_string(),
                    event_time_unix_nanos: now,
                    record_kind: Some("market".to_string()),
                    record_id: Some(id.to_string()),
                    market_id: Some(id.clone()),
                    instrument_id: Some(previous.instrument_id.clone()),
                    listing_id: previous.listing_id.clone(),
                    exchange_id: Some(previous.exchange_id.clone()),
                    venue_symbol: previous.venue_symbol.clone(),
                    previous_status: Some(previous.status),
                    current_status: Some(ReferenceStatus::Delisted),
                    previous_symbol: None,
                    current_symbol: None,
                    ..LifecycleEvent::default()
                });
                // Keep the delisted record in the catalog so consumers can resolve it.
                delisted_records.push((id.clone(), delisted));
            }
        }
        for (id, market) in delisted_records {
            next_markets.insert(id, market);
        }
        self.markets = next_markets;
        self.event_sequence = Sequence::new(
            self.event_sequence
                .get()
                .saturating_add(events.len() as u64),
        );
        if previous_exchanges != self.exchanges
            || previous_venues != self.venues
            || previous_assets != self.assets
            || previous_instruments != self.instruments
            || previous_listings != self.listings
            || previous_markets != self.markets
            || previous_venue_listings != self.venue_listings
            || previous_venue_markets != self.venue_markets
            || previous_memberships != self.provider_catalog_memberships
            || previous_venue_identifier_mappings != self.venue_identifier_mappings
        {
            self.generation = Generation::new(self.generation.get().saturating_add(1));
        }
        events.finish(self.generation);
        // The returned batch is complete for durable audit/publication. Only
        // the bounded recent tail is copied into the in-memory history.
        let tail_start = events.len().saturating_sub(Self::LIFECYCLE_HISTORY_LIMIT);
        let tail = events.iter().skip(tail_start);
        self.retain_recent_lifecycle_events(Self::LIFECYCLE_HISTORY_LIMIT - tail.len());
        self.lifecycle_events.extend(tail);
        events
    }

    pub fn retain_recent_lifecycle_events(&mut self, limit: usize) {
        if self.lifecycle_events.len() <= limit {
            return;
        }
        let keep_from = self.lifecycle_events.len() - limit;
        self.lifecycle_events.drain(..keep_from);
    }

    pub fn upsert_manual_asset(
        &mut self,
        asset: Asset,
        policy: &ManualUpsertPolicy,
        now: UnixNanos,
    ) -> ReferenceResult<Option<LifecycleEvent>> {
        if policy.reject_provider_owned {
            if let Some(existing) = self.assets.get(asset.asset_id.as_str()) {
                reject_provider_owned_upsert(
                    existing.source_id.as_deref(),
                    "asset",
                    asset.asset_id.as_str(),
                )?;
            }
        }
        let mut candidate = self.provider_catalog();
        candidate
            .assets
            .retain(|value| value.asset_id != asset.asset_id);
        candidate.assets.push(asset.clone());
        candidate.validate()?;
        if self.assets.get(asset.asset_id.as_str()) == Some(&asset) {
            return Ok(None);
        }
        let sequence = self.next_event_sequence();
        let asset_id = asset.asset_id.clone();
        self.assets.insert(asset_id.to_string(), asset);
        Ok(Some(self.append_manual_event(
            sequence,
            "asset_changed",
            "asset",
            asset_id.to_string(),
            policy,
            now,
        )))
    }

    pub fn upsert_manual_instrument(
        &mut self,
        instrument: Instrument,
        policy: &ManualUpsertPolicy,
        now: UnixNanos,
    ) -> ReferenceResult<Option<LifecycleEvent>> {
        if policy.reject_provider_owned {
            if let Some(existing) = self.instruments.get(&instrument.instrument_id) {
                reject_provider_owned_upsert(
                    existing.source_id.as_deref(),
                    "instrument",
                    instrument.instrument_id.to_string().as_str(),
                )?;
            }
        }
        let mut candidate = self.provider_catalog();
        candidate
            .instruments
            .retain(|value| value.instrument_id != instrument.instrument_id);
        candidate.instruments.push(instrument.clone());
        candidate.validate()?;
        if self.instruments.get(&instrument.instrument_id) == Some(&instrument) {
            return Ok(None);
        }
        let sequence = self.next_event_sequence();
        let instrument_id = instrument.instrument_id.clone();
        self.instruments.insert(instrument_id.clone(), instrument);
        Ok(Some(self.append_manual_event(
            sequence,
            "instrument_changed",
            "instrument",
            instrument_id.to_string(),
            policy,
            now,
        )))
    }

    pub fn upsert_manual_listing(
        &mut self,
        listing: Listing,
        policy: &ManualUpsertPolicy,
        now: UnixNanos,
    ) -> ReferenceResult<Option<LifecycleEvent>> {
        if policy.reject_provider_owned {
            if let Some(existing) = self.listings.get(&listing.listing_id) {
                reject_provider_owned_upsert(
                    existing.source_id.as_deref(),
                    "listing",
                    listing.listing_id.as_str(),
                )?;
            }
        }
        let mut candidate = self.provider_catalog();
        candidate
            .listings
            .retain(|value| value.listing_id != listing.listing_id);
        candidate.listings.push(listing.clone());
        candidate.validate()?;
        if self.listings.get(&listing.listing_id) == Some(&listing) {
            return Ok(None);
        }
        let sequence = self.next_event_sequence();
        let listing_id = listing.listing_id.clone();
        self.listings.insert(listing_id.clone(), listing);
        Ok(Some(self.append_manual_event(
            sequence,
            "listing_changed",
            "listing",
            listing_id.to_string(),
            policy,
            now,
        )))
    }

    pub fn provider_catalog(&self) -> ProviderCatalog {
        ProviderCatalog {
            venues: self.venues.values().cloned().collect(),
            exchanges: self.exchanges.values().cloned().collect(),
            assets: self.assets.values().cloned().collect(),
            instruments: self.instruments.values().cloned().collect(),
            listings: self.listings.values().cloned().collect(),
            markets: self.markets.values().cloned().collect(),
            venue_listings: self.venue_listings.values().cloned().collect(),
            venue_markets: self.venue_markets.values().cloned().collect(),
            provider_catalog_memberships: self
                .provider_catalog_memberships
                .values()
                .cloned()
                .collect(),
            venue_identifier_mappings: self.venue_identifier_mappings.values().cloned().collect(),
        }
    }

    fn next_event_sequence(&mut self) -> Sequence {
        self.generation += 1;
        self.event_sequence += 1;
        self.event_sequence
    }

    fn append_manual_event(
        &mut self,
        sequence: Sequence,
        event_type: &str,
        record_kind: &str,
        record_id: String,
        policy: &ManualUpsertPolicy,
        now: UnixNanos,
    ) -> LifecycleEvent {
        let event = LifecycleEvent {
            event_id: format!("reference:{sequence:020}"),
            event_type: event_type.into(),
            event_time_unix_nanos: now,
            record_kind: Some(record_kind.into()),
            record_id: Some(record_id),
            operation: Some("upsert".into()),
            provenance: Some(policy.provenance.clone()),
            conflict_policy: Some(policy.conflict_policy.clone()),
            generation: self.generation,
            ..LifecycleEvent::default()
        };
        self.lifecycle_events.push(event.clone());
        event
    }
}

/// Transitional adapter for current provider mappers that still emit v2
/// Exchange/Listing/Market records. Explicit v3 facts always win. This lives
/// at the Reference domain boundary so persistence and publication observe
/// the same canonical records during the staged migration.
fn materialize_v3_compatibility(catalog: &mut ProviderCatalog) {
    let mut venue_ids = catalog
        .venues
        .iter()
        .map(|value| value.venue_id.clone())
        .collect::<BTreeSet<_>>();
    for exchange in &catalog.exchanges {
        let key = exchange
            .exchange_id
            .as_str()
            .strip_prefix("exchange:")
            .unwrap_or(exchange.exchange_id.as_str());
        let Ok(venue_id) = VenueId::new(format!("venue:{key}")) else {
            continue;
        };
        if venue_ids.contains(&venue_id) {
            continue;
        }
        let mut roles = BTreeSet::new();
        if catalog
            .listings
            .iter()
            .any(|value| value.exchange_id == exchange.exchange_id)
        {
            roles.insert(crate::domain::VenueRole::Listing);
        }
        if catalog
            .markets
            .iter()
            .any(|value| value.exchange_id == exchange.exchange_id)
        {
            roles.insert(crate::domain::VenueRole::Execution);
        }
        if roles.is_empty() {
            continue;
        }
        venue_ids.insert(venue_id.clone());
        catalog.venues.push(Venue {
            venue_id,
            name: exchange.name.clone(),
            venue_kind: crate::domain::VenueKind::RegulatedExchange,
            roles,
            mic: None,
            operating_mic: None,
            parent_venue_id: None,
            jurisdiction: None,
            status: exchange.status,
        });
    }

    let mut listing_ids = catalog
        .venue_listings
        .iter()
        .map(|value| value.listing_id.clone())
        .collect::<BTreeSet<_>>();
    for listing in &catalog.listings {
        if !listing_ids.insert(listing.listing_id.clone()) {
            continue;
        }
        let key = listing
            .exchange_id
            .as_str()
            .strip_prefix("exchange:")
            .unwrap_or(listing.exchange_id.as_str());
        let Ok(listing_venue_id) = VenueId::new(format!("venue:{key}")) else {
            continue;
        };
        catalog.venue_listings.push(VenueListing {
            source_id: listing.source_id.clone(),
            listing_id: listing.listing_id.clone(),
            instrument_id: listing.instrument_id.clone(),
            listing_venue_id,
            market_segment_id: None,
            listing_symbol: listing.exchange_symbol.clone(),
            listing_role: crate::domain::ListingRole::Unknown,
            status: listing.status,
            effective_from_unix_nanos: listing.effective_from_unix_nanos,
            effective_to_unix_nanos: listing.effective_to_unix_nanos,
        });
    }

    let mut market_ids = catalog
        .venue_markets
        .iter()
        .map(|value| value.market_id.clone())
        .collect::<BTreeSet<_>>();
    for market in &catalog.markets {
        if !market_ids.insert(market.market_id.clone()) {
            continue;
        }
        let key = market
            .exchange_id
            .as_str()
            .strip_prefix("exchange:")
            .unwrap_or(market.exchange_id.as_str());
        let Ok(execution_venue_id) = VenueId::new(format!("venue:{key}")) else {
            continue;
        };
        catalog.venue_markets.push(VenueMarket {
            market_id: market.market_id.clone(),
            instrument_id: market.instrument_id.clone(),
            execution_venue_id,
            origin_listing_id: market.listing_id.clone(),
            market_segment_id: None,
            venue_symbol: market.venue_symbol.clone(),
            trading_calendar_id: None,
            trading_session_ids: Vec::new(),
            base_asset_id: market.base_asset_id.clone(),
            quote_asset_id: market.quote_asset_id.clone(),
            status: market.status,
            trading_rules: crate::domain::TradingRules {
                price_tick: market.price_tick,
                quantity_tick: market.quantity_tick,
                price_precision: market.price_precision,
                quantity_precision: market.quantity_precision,
                minimum_quantity: market.minimum_quantity,
                minimum_notional: market.minimum_notional,
                contract_size: market.contract_size,
            },
            effective_from_unix_nanos: market.effective_from_unix_nanos,
            effective_to_unix_nanos: market.effective_to_unix_nanos,
        });
    }
}

fn reject_provider_owned_upsert(
    source_id: Option<&str>,
    record_kind: &str,
    record_id: &str,
) -> ReferenceResult<()> {
    if let Some(source_id) = source_id {
        return Err(ReferenceError::Invalid(format!(
            "cannot overwrite provider-owned {record_kind} {record_id} from source {source_id}"
        )));
    }
    Ok(())
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    #[test]
    fn compact_record_events_expand_with_stable_context_and_full_details() {
        let mut batch = super::CatalogEventBatch::new(10.into(), 7.into());
        batch.push_record("asset", "asset_added", "asset:USD");
        batch.push(super::LifecycleEvent {
            event_id: "reference:00000000000000000009".into(),
            event_type: "market_changed".into(),
            event_time_unix_nanos: 10.into(),
            previous_symbol: Some("BEFORE".into()),
            current_symbol: Some("AFTER".into()),
            ..Default::default()
        });
        batch.finish(3.into());
        let events = batch.iter().collect::<Vec<_>>();
        assert_eq!(events, batch.iter().collect::<Vec<_>>());
        assert_eq!(events[0].event_id, "reference:00000000000000000008");
        assert_eq!(events[0].record_kind.as_deref(), Some("asset"));
        assert_eq!(events[0].record_id.as_deref(), Some("asset:USD"));
        assert_eq!(events[1].previous_symbol.as_deref(), Some("BEFORE"));
        assert_eq!(events[1].current_symbol.as_deref(), Some("AFTER"));
        assert!(events.iter().all(
            |event| event.generation.get() == 3 && event.operation.as_deref() == Some("upsert")
        ));
        assert!(
            std::mem::size_of::<super::CatalogChange>()
                < std::mem::size_of::<super::LifecycleEvent>() / 3
        );
    }

    use kairos_primitives::reference::{
        AssetClass, AssetId, ExchangeId, InstrumentId, ListingId, MarketId, ReferenceSourceId,
        ReferenceStatus, Symbol,
    };
    use kairos_primitives::time::UnixNanos;

    use super::{
        Asset, Exchange, Instrument, LifecycleEvent, Listing, ManualUpsertPolicy, Market,
        ProviderCatalog, ReferenceCatalog, materialize_v3_compatibility,
    };

    fn instrument_id(value: &str) -> InstrumentId {
        InstrumentId::new(value).unwrap()
    }

    fn listing_id(value: &str) -> ListingId {
        ListingId::new(value).unwrap()
    }

    fn market_id(value: &str) -> MarketId {
        MarketId::new(value).unwrap()
    }

    fn manual_policy(conflict_policy: &str, reject_provider_owned: bool) -> ManualUpsertPolicy {
        ManualUpsertPolicy::new("manual", conflict_policy, reject_provider_owned)
    }

    fn asset_id(value: &str) -> AssetId {
        AssetId::new(value).unwrap()
    }

    #[test]
    fn mapping_only_changes_advance_generation_and_lazy_event_context() {
        let mut incoming = catalog_with_market("active");
        incoming
            .venue_identifier_mappings
            .push(super::VenueIdentifierMapping {
                source_id: ReferenceSourceId::new("massive-equity").unwrap(),
                provider: kairos_primitives::market::Provider::new("massive").unwrap(),
                provider_product: "equity".into(),
                identifier_kind: crate::domain::VenueIdentifierKind::Exchange,
                identifier: "19".into(),
                venue_id: kairos_primitives::reference::VenueId::new("venue:test").unwrap(),
                status: ReferenceStatus::Active,
            });
        let mut catalog = ReferenceCatalog::default();
        catalog.apply_events(incoming.clone(), 1.into());
        for remove in [false, true] {
            let previous = catalog.generation.get();
            if remove {
                incoming.venue_identifier_mappings.clear();
            } else {
                incoming.venue_identifier_mappings[0].identifier = "20".into();
            }
            let events = catalog.apply_events(incoming.clone(), 2.into());
            assert_eq!(catalog.generation.get(), previous + 1);
            assert_eq!(events.len(), 1);
            let event = events.iter().next().unwrap();
            assert_eq!(event.event_type, "venue_changed");
            assert_eq!(event.generation, catalog.generation);
            assert_eq!(event.record_id.as_deref(), Some("venue:test"));
        }
    }

    fn catalog_with_market(status: &str) -> ProviderCatalog {
        ProviderCatalog {
            exchanges: vec![Exchange {
                exchange_id: ExchangeId::new("exchange:test").unwrap(),
                name: "Test Exchange".into(),
                status: "active".into(),
                ..Default::default()
            }],
            instruments: vec![Instrument {
                instrument_id: instrument_id("instrument:test"),
                symbol: Symbol::new("TEST").unwrap(),
                instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
                status: status.into(),
                ..Default::default()
            }],
            listings: vec![Listing {
                listing_id: listing_id("listing:test"),
                instrument_id: instrument_id("instrument:test"),
                exchange_id: ExchangeId::new("exchange:test").unwrap(),
                exchange_symbol: Symbol::new("TEST").unwrap(),
                status: status.into(),
                effective_from_unix_nanos: 1.into(),
                ..Default::default()
            }],
            markets: vec![Market {
                market_id: market_id("market:test"),
                instrument_id: instrument_id("instrument:test"),
                listing_id: Some(listing_id("listing:test")),
                exchange_id: ExchangeId::new("exchange:test").unwrap(),
                instrument_kind: kairos_primitives::reference::InstrumentKind::Spot,
                venue_symbol: Some(kairos_primitives::reference::Symbol::new("TEST").unwrap()),
                status: status.into(),
                effective_from_unix_nanos: 1.into(),
                ..Default::default()
            }],
            ..Default::default()
        }
    }

    #[test]
    fn canonical_venue_identity_survives_withdrawal_and_recovers() {
        let mut incoming = catalog_with_market("active");
        materialize_v3_compatibility(&mut incoming);
        // Exercise canonical records without a legacy Exchange/Listing/Market authority.
        incoming.exchanges.clear();
        incoming.listings.clear();
        incoming.markets.clear();
        let mut catalog = ReferenceCatalog::default();
        catalog.apply(incoming.clone(), UnixNanos::new(10));
        let withdrawn = catalog.apply(ProviderCatalog::default(), UnixNanos::new(20));
        let venue = catalog.venues.values().next().unwrap();
        let listing = catalog.venue_listings.values().next().unwrap();
        let market = catalog.venue_markets.values().next().unwrap();
        assert_eq!(venue.status, ReferenceStatus::Inactive);
        assert_eq!(listing.status, ReferenceStatus::Inactive);
        assert_eq!(market.status, ReferenceStatus::Inactive);
        assert_eq!(listing.effective_to_unix_nanos, Some(UnixNanos::new(20)));
        assert_eq!(market.effective_to_unix_nanos, Some(UnixNanos::new(20)));
        for kind in ["venue", "venue_listing", "venue_market"] {
            assert_eq!(
                withdrawn
                    .iter()
                    .filter(|event| event.record_kind.as_deref() == Some(kind))
                    .count(),
                1
            );
        }
        let generation = catalog.generation;
        assert!(
            catalog
                .apply(ProviderCatalog::default(), UnixNanos::new(30))
                .is_empty()
        );
        assert_eq!(catalog.generation, generation);
        assert_eq!(
            catalog
                .venue_markets
                .values()
                .next()
                .unwrap()
                .effective_to_unix_nanos,
            Some(UnixNanos::new(20))
        );
        catalog.apply(incoming, UnixNanos::new(40));
        assert_eq!(
            catalog.venues.values().next().unwrap().status,
            ReferenceStatus::Active
        );
        assert_eq!(
            catalog.venue_listings.values().next().unwrap().status,
            ReferenceStatus::Active
        );
        let market = catalog.venue_markets.values().next().unwrap();
        assert_eq!(market.status, ReferenceStatus::Active);
        assert_eq!(market.effective_to_unix_nanos, None);
    }

    #[test]
    fn canonical_withdrawal_preserves_explicit_delisting() {
        let mut incoming = catalog_with_market("delisted");
        materialize_v3_compatibility(&mut incoming);
        incoming.exchanges.clear();
        incoming.listings.clear();
        incoming.markets.clear();
        for venue in &mut incoming.venues {
            venue.status = ReferenceStatus::Delisted;
        }
        for listing in &mut incoming.venue_listings {
            listing.effective_to_unix_nanos = Some(UnixNanos::new(5));
        }
        for market in &mut incoming.venue_markets {
            market.effective_to_unix_nanos = Some(UnixNanos::new(5));
        }
        let mut catalog = ReferenceCatalog::default();
        catalog.apply(incoming, UnixNanos::new(10));
        let venues = catalog.venues.clone();
        let listings = catalog.venue_listings.clone();
        let markets = catalog.venue_markets.clone();
        let events = catalog.apply(ProviderCatalog::default(), UnixNanos::new(20));
        assert_eq!(catalog.venues, venues);
        assert_eq!(catalog.venue_listings, listings);
        assert_eq!(catalog.venue_markets, markets);
        assert!(events.iter().all(|event| !matches!(
            event.record_kind.as_deref(),
            Some("venue" | "venue_listing" | "venue_market")
        )));
    }

    #[test]
    fn delisted_market_emits_only_one_delisted_event() {
        let mut catalog = ReferenceCatalog::default();
        assert_eq!(
            catalog
                .apply(catalog_with_market("active"), 10.into())
                .len(),
            7
        );
        assert_eq!(
            catalog.apply(ProviderCatalog::default(), 20.into()).len(),
            7
        );
        assert!(
            catalog
                .apply(ProviderCatalog::default(), 30.into())
                .is_empty()
        );
        assert_eq!(catalog.lifecycle_events.len(), 14);
        let retained = &catalog.markets[&market_id("market:test")];
        assert_eq!(retained.status, ReferenceStatus::Delisted);
        assert_eq!(retained.effective_to_unix_nanos, Some(UnixNanos::new(20)));
        assert_eq!(
            catalog
                .lifecycle_events
                .iter()
                .filter(|event| event.event_type == "delisted")
                .count(),
            1
        );
    }

    #[test]
    fn apply_repairs_stale_canonical_exchange_names() {
        let mut catalog = ReferenceCatalog::default();
        let events = catalog.apply(
            ProviderCatalog {
                exchanges: vec![
                    Exchange {
                        exchange_id: ExchangeId::new("exchange:arcx").unwrap(),
                        name: "Exchange".into(),
                        status: "active".into(),
                        ..Default::default()
                    },
                    Exchange {
                        exchange_id: ExchangeId::new("exchange:bats").unwrap(),
                        name: "Exchange".into(),
                        status: "active".into(),
                        ..Default::default()
                    },
                ],
                ..Default::default()
            },
            10.into(),
        );

        assert_eq!(catalog.exchanges["exchange:arcx"].name, "NYSE Arca");
        assert_eq!(catalog.exchanges["exchange:bats"].name, "Cboe BZX Exchange");
        assert_eq!(events.len(), 2);
    }

    #[test]
    fn delisted_market_can_be_relisted() {
        let mut catalog = ReferenceCatalog::default();
        catalog.apply(catalog_with_market("active"), 10.into());
        catalog.apply(ProviderCatalog::default(), 20.into());
        let events = catalog.apply(catalog_with_market("active"), 30.into());
        assert_eq!(events.len(), 7);
        assert!(
            events
                .iter()
                .any(|event| event.event_type == "status_changed")
        );
        assert_eq!(catalog.markets["market:test"].status, "active".into());
    }

    #[test]
    fn validation_rejects_unresolved_reference_relationships() {
        let catalog = ProviderCatalog {
            listings: vec![Listing {
                listing_id: listing_id("listing:missing"),
                instrument_id: instrument_id("instrument:missing"),
                exchange_id: ExchangeId::new("exchange:missing").unwrap(),
                exchange_symbol: Symbol::new("MISSING").unwrap(),
                status: "active".into(),
                effective_from_unix_nanos: 1.into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let error = catalog.validate().unwrap_err().to_string();
        assert!(error.contains("missing instrument"));
    }

    #[test]
    fn validation_accepts_a_non_listing_market() {
        let mut catalog = catalog_with_market("active");
        catalog.listings.clear();
        catalog.markets[0].listing_id = None;
        catalog.instruments[0] = Instrument {
            instrument_id: instrument_id("instrument:test"),
            symbol: Symbol::new("TEST").unwrap(),
            instrument_type: kairos_primitives::reference::InstrumentKind::Spot,
            status: "active".into(),
            ..Default::default()
        };

        catalog.validate().unwrap();
    }

    #[test]
    fn validation_rejects_incomplete_option_identity() {
        let error = ProviderCatalog {
            instruments: vec![Instrument {
                instrument_id: instrument_id("instrument:option"),
                symbol: kairos_primitives::reference::Symbol::new("BTC-OPT").unwrap(),
                instrument_type: kairos_primitives::reference::InstrumentKind::Option,
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        }
        .validate()
        .unwrap_err()
        .to_string();
        assert!(error.contains("requires expiry, strike and call/put"));
    }

    #[test]
    fn manual_asset_upsert_marks_curated_event_metadata() {
        let mut catalog = ReferenceCatalog::default();
        let event = catalog
            .upsert_manual_asset(
                Asset {
                    asset_id: asset_id("asset:SOL"),
                    code: Symbol::new("SOL").unwrap(),
                    asset_class: AssetClass::Crypto,
                    status: "active".into(),
                    ..Default::default()
                },
                &manual_policy("reject_provider_owned", true),
                42.into(),
            )
            .unwrap()
            .expect("asset changed");

        assert_eq!(catalog.generation, 1.into());
        assert_eq!(catalog.event_sequence, 1.into());
        assert_eq!(event.event_type, "asset_changed");
        assert_eq!(event.record_kind.as_deref(), Some("asset"));
        assert_eq!(event.record_id.as_deref(), Some("asset:SOL"));
        assert_eq!(event.operation.as_deref(), Some("upsert"));
        assert_eq!(event.provenance.as_deref(), Some("manual"));
        assert_eq!(
            event.conflict_policy.as_deref(),
            Some("reject_provider_owned")
        );
    }

    #[test]
    fn manual_upsert_noops_when_record_is_unchanged() {
        let asset = Asset {
            asset_id: asset_id("asset:SOL"),
            code: Symbol::new("SOL").unwrap(),
            asset_class: AssetClass::Crypto,
            status: "active".into(),
            ..Default::default()
        };
        let mut catalog = ReferenceCatalog::default();
        catalog
            .upsert_manual_asset(
                asset.clone(),
                &manual_policy("allow_overwrite", false),
                42.into(),
            )
            .unwrap();

        let event =
            catalog.upsert_manual_asset(asset, &manual_policy("allow_overwrite", false), 43.into());

        assert!(event.unwrap().is_none());
        assert_eq!(catalog.generation, 1.into());
        assert_eq!(catalog.event_sequence, 1.into());
        assert_eq!(catalog.lifecycle_events.len(), 1);
    }

    #[test]
    fn apply_returns_complete_events_without_copying_all_into_recent_history() {
        let count = ReferenceCatalog::LIFECYCLE_HISTORY_LIMIT + 17;
        let incoming = ProviderCatalog {
            exchanges: (0..count)
                .map(|index| Exchange {
                    exchange_id: ExchangeId::new(format!("exchange:{index:06}")).unwrap(),
                    name: format!("Test {index}"),
                    status: ReferenceStatus::Active,
                    ..Default::default()
                })
                .collect(),
            ..Default::default()
        };
        let mut catalog = ReferenceCatalog::default();
        let events = catalog.apply(incoming, UnixNanos::new(10));
        assert_eq!(events.len(), count);
        assert_eq!(
            catalog.lifecycle_events.len(),
            ReferenceCatalog::LIFECYCLE_HISTORY_LIMIT
        );
        assert_eq!(catalog.lifecycle_events, events[17..]);
        assert_eq!(catalog.event_sequence.get(), count as u64);
    }

    #[test]
    fn retain_recent_lifecycle_events_keeps_tail_window() {
        let mut catalog = ReferenceCatalog {
            lifecycle_events: (1..=5)
                .map(|sequence| LifecycleEvent {
                    event_id: format!("reference:{sequence:020}"),
                    ..LifecycleEvent::default()
                })
                .collect(),
            ..ReferenceCatalog::default()
        };

        catalog.retain_recent_lifecycle_events(2);

        assert_eq!(catalog.lifecycle_events.len(), 2);
        assert_eq!(
            catalog.lifecycle_events[0].event_id,
            "reference:00000000000000000004"
        );
        assert_eq!(
            catalog.lifecycle_events[1].event_id,
            "reference:00000000000000000005"
        );
    }

    #[test]
    fn manual_upsert_rejects_provider_owned_record_when_requested() {
        let mut catalog = ReferenceCatalog::default();
        catalog.assets.insert(
            "asset:BTC".into(),
            Asset {
                source_id: Some(ReferenceSourceId::new("binance-spot").unwrap()),
                asset_id: asset_id("asset:BTC"),
                code: Symbol::new("BTC").unwrap(),
                asset_class: AssetClass::Crypto,
                status: "active".into(),
                ..Default::default()
            },
        );

        let error = catalog
            .upsert_manual_asset(
                Asset {
                    asset_id: asset_id("asset:BTC"),
                    code: Symbol::new("BTC").unwrap(),
                    asset_class: AssetClass::Crypto,
                    status: "active".into(),
                    ..Default::default()
                },
                &manual_policy("reject_provider_owned", true),
                42.into(),
            )
            .unwrap_err()
            .to_string();

        assert!(error.contains("provider-owned asset asset:BTC"));
        assert!(error.contains("binance-spot"));
    }

    #[test]
    fn manual_listing_upsert_rejects_provider_owned_record_when_requested() {
        let mut catalog = ReferenceCatalog::default();
        catalog.listings.insert(
            listing_id("listing:binance:spot:BTC:USDT"),
            Listing {
                source_id: Some(ReferenceSourceId::new("binance-spot").unwrap()),
                listing_id: listing_id("listing:binance:spot:BTC:USDT"),
                instrument_id: instrument_id("instrument:spot:BTC-USDT"),
                exchange_id: ExchangeId::new("exchange:binance").unwrap(),
                exchange_symbol: Symbol::new("BTCUSDT").unwrap(),
                status: "active".into(),
                effective_from_unix_nanos: 1.into(),
                ..Default::default()
            },
        );

        let error = catalog
            .upsert_manual_listing(
                Listing {
                    listing_id: listing_id("listing:binance:spot:BTC:USDT"),
                    instrument_id: instrument_id("instrument:spot:BTC-USDT"),
                    exchange_id: ExchangeId::new("exchange:binance").unwrap(),
                    exchange_symbol: Symbol::new("BTC-USDT").unwrap(),
                    status: "active".into(),
                    effective_from_unix_nanos: 1.into(),
                    ..Default::default()
                },
                &manual_policy("reject_provider_owned", true),
                42.into(),
            )
            .unwrap_err()
            .to_string();

        assert!(error.contains("provider-owned listing listing:binance:spot:BTC:USDT"));
        assert!(error.contains("binance-spot"));
    }
}

impl LifecycleEvent {
    fn listed(market: &Market, now: UnixNanos, sequence: u64) -> Self {
        Self {
            event_id: format!("reference:{sequence:020}"),
            event_type: "listed".to_string(),
            event_time_unix_nanos: now,
            record_kind: Some("market".to_string()),
            record_id: Some(market.market_id.to_string()),
            market_id: Some(market.market_id.clone()),
            instrument_id: Some(market.instrument_id.clone()),
            listing_id: market.listing_id.clone(),
            exchange_id: Some(market.exchange_id.clone()),
            venue_symbol: market.venue_symbol.clone(),
            current_status: Some(market.status),
            ..Self::default()
        }
    }
}

fn record_event(
    record_kind: &str,
    event_type: &str,
    record_id: &str,
    now: UnixNanos,
    sequence: u64,
) -> LifecycleEvent {
    LifecycleEvent {
        event_id: format!("reference:{sequence:020}"),
        event_type: event_type.to_string(),
        event_time_unix_nanos: now,
        record_kind: Some(record_kind.to_string()),
        record_id: Some(record_id.to_string()),
        ..LifecycleEvent::default()
    }
}
