//! Reference catalog aggregate and lifecycle reconciliation.

use std::collections::BTreeMap;

use kairos_primitives::reference::{InstrumentId, ListingId, MarketId, ReferenceStatus};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use super::{Asset, Entity, Instrument, LifecycleEvent, Listing, Market, ProviderCatalog};

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceCatalog {
    pub entities: BTreeMap<String, Entity>,
    pub assets: BTreeMap<String, Asset>,
    pub instruments: BTreeMap<InstrumentId, Instrument>,
    pub listings: BTreeMap<ListingId, Listing>,
    pub markets: BTreeMap<MarketId, Market>,
    pub lifecycle_events: Vec<LifecycleEvent>,
    pub generation: Generation,
    pub event_sequence: Sequence,
}

impl ReferenceCatalog {
    pub fn apply(&mut self, incoming: ProviderCatalog, now: UnixNanos) -> Vec<LifecycleEvent> {
        let previous_entities = std::mem::take(&mut self.entities);
        let previous_assets = std::mem::take(&mut self.assets);
        let previous_instruments = std::mem::take(&mut self.instruments);
        let previous_listings = std::mem::take(&mut self.listings);
        let previous_markets = std::mem::take(&mut self.markets);
        self.entities = incoming
            .entities
            .into_iter()
            .map(|v| (v.entity_id.clone(), v))
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
        for (id, previous) in &previous_entities {
            self.entities.entry(id.clone()).or_insert_with(|| {
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

        diff_records!("entity", previous_entities, self.entities);
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
        if previous_entities != self.entities
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

    pub fn active_market_count(&self) -> usize {
        self.markets
            .values()
            .filter(|market| {
                matches!(
                    market.status,
                    ReferenceStatus::Active | ReferenceStatus::Trading
                )
            })
            .count()
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use kairos_primitives::reference::{Exchange, InstrumentId, ListingId, MarketId, Symbol};

    use super::{Entity, Instrument, Listing, Market, ProviderCatalog, ReferenceCatalog};

    fn instrument_id(value: &str) -> InstrumentId {
        InstrumentId::new(value).unwrap()
    }

    fn listing_id(value: &str) -> ListingId {
        ListingId::new(value).unwrap()
    }

    fn market_id(value: &str) -> MarketId {
        MarketId::new(value).unwrap()
    }

    fn catalog_with_market(status: &str) -> ProviderCatalog {
        ProviderCatalog {
            entities: vec![Entity {
                entity_id: "exchange:test".into(),
                entity_type: "exchange".into(),
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
                exchange_id: Exchange::new("exchange:test").unwrap(),
                exchange_symbol: Symbol::new("TEST").unwrap(),
                status: status.into(),
                effective_from_unix_nanos: 1.into(),
                ..Default::default()
            }],
            markets: vec![Market {
                market_id: market_id("market:test"),
                instrument_id: instrument_id("instrument:test"),
                listing_id: Some(listing_id("listing:test")),
                exchange_id: Exchange::new("exchange:test").unwrap(),
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
                exchange_id: Exchange::new("exchange:missing").unwrap(),
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
