//! Reference catalog aggregate and lifecycle reconciliation.

use std::collections::BTreeMap;

use kairos_primitives::reference::{
    ExchangeId, InstrumentId, ListingId, MarketId, ReferenceStatus,
};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use super::{
    Asset, Exchange, Instrument, LifecycleEvent, Listing, Market, ProviderCatalog, ReferenceError,
    ReferenceResult,
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
    pub exchanges: BTreeMap<ExchangeId, Exchange>,
    pub assets: BTreeMap<String, Asset>,
    pub instruments: BTreeMap<InstrumentId, Instrument>,
    pub listings: BTreeMap<ListingId, Listing>,
    pub markets: BTreeMap<MarketId, Market>,
    pub lifecycle_events: Vec<LifecycleEvent>,
    pub generation: Generation,
    pub event_sequence: Sequence,
}

impl ReferenceCatalog {
    pub fn apply(&mut self, mut incoming: ProviderCatalog, now: UnixNanos) -> Vec<LifecycleEvent> {
        for exchange in &mut incoming.exchanges {
            exchange.normalize_canonical_name();
        }
        let previous_exchanges = std::mem::take(&mut self.exchanges);
        let previous_assets = std::mem::take(&mut self.assets);
        let previous_instruments = std::mem::take(&mut self.instruments);
        let previous_listings = std::mem::take(&mut self.listings);
        let previous_markets = std::mem::take(&mut self.markets);
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

        // Canonical identity is retained after a provider withdrawal. A
        // missing provider fact is expressed as an effective lifecycle
        // transition, never as a hard delete that makes historical identity
        // or an already-committed event impossible to resolve.
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
        let mut events = Vec::new();

        macro_rules! diff_records {
            ($kind:literal, $previous:expr, $current:expr) => {
                for (id, next) in &$current {
                    let event_type = match $previous.get(id) {
                        None => Some(concat!($kind, "_added")),
                        Some(previous) if previous != next => Some(concat!($kind, "_changed")),
                        _ => None,
                    };
                    if let Some(event_type) = event_type {
                        events.push(record_event(
                            $kind,
                            event_type,
                            id,
                            now,
                            self.event_sequence.get() + events.len() as u64 + 1,
                        ));
                    }
                }
            };
        }

        diff_records!("exchange", previous_exchanges, self.exchanges);
        diff_records!("asset", previous_assets, self.assets);
        diff_records!("instrument", previous_instruments, self.instruments);
        diff_records!("listing", previous_listings, self.listings);

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
            || previous_assets != self.assets
            || previous_instruments != self.instruments
            || previous_listings != self.listings
            || previous_markets != self.markets
        {
            self.generation = Generation::new(self.generation.get().saturating_add(1));
        }
        for event in &mut events {
            event.operation = Some("upsert".into());
            event.generation = self.generation;
        }
        self.lifecycle_events.extend(events.iter().cloned());
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
            exchanges: self.exchanges.values().cloned().collect(),
            assets: self.assets.values().cloned().collect(),
            instruments: self.instruments.values().cloned().collect(),
            listings: self.listings.values().cloned().collect(),
            markets: self.markets.values().cloned().collect(),
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
    use kairos_primitives::reference::{
        AssetClass, AssetId, ExchangeId, InstrumentId, ListingId, MarketId, ReferenceSourceId,
        Symbol,
    };

    use super::{
        Asset, Exchange, Instrument, LifecycleEvent, Listing, ManualUpsertPolicy, Market,
        ProviderCatalog, ReferenceCatalog,
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
    fn delisted_market_emits_only_one_delisted_event() {
        let mut catalog = ReferenceCatalog::default();
        assert_eq!(
            catalog
                .apply(catalog_with_market("active"), 10.into())
                .len(),
            4
        );
        assert_eq!(
            catalog.apply(ProviderCatalog::default(), 20.into()).len(),
            4
        );
        assert!(
            catalog
                .apply(ProviderCatalog::default(), 30.into())
                .is_empty()
        );
        assert_eq!(catalog.lifecycle_events.len(), 8);
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
        assert_eq!(events.len(), 4);
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
