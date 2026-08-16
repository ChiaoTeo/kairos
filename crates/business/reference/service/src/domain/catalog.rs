//! Reference catalog aggregate and lifecycle reconciliation.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use kairos_domain_types::{
    ExecutionAccessId, Generation, InstrumentId, ListingId, MarketId, ReferenceStatus, Sequence,
    UnixNanos,
};
use serde::{Deserialize, Serialize};

use super::{
    Asset, Entity, ExecutionAccess, FinancialProduct, Instrument, LifecycleEvent, Listing, Market,
    MarketDataAccess, ProviderCatalog,
};

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceCatalog {
    pub entities: BTreeMap<String, Entity>,
    pub assets: BTreeMap<String, Asset>,
    pub instruments: BTreeMap<InstrumentId, Instrument>,
    pub listings: BTreeMap<ListingId, Listing>,
    pub markets: BTreeMap<MarketId, Market>,
    pub financial_products: BTreeMap<String, FinancialProduct>,
    #[serde(default)]
    pub execution_accesses: BTreeMap<ExecutionAccessId, ExecutionAccess>,
    #[serde(default)]
    pub market_data_accesses: BTreeMap<String, MarketDataAccess>,
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
        let previous_financial_products = std::mem::take(&mut self.financial_products);
        let previous_execution_accesses = std::mem::take(&mut self.execution_accesses);
        let previous_market_data_accesses = std::mem::take(&mut self.market_data_accesses);
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
        self.financial_products = incoming
            .financial_products
            .into_iter()
            .map(|v| (v.product_id.clone(), v))
            .collect();
        self.execution_accesses = incoming
            .execution_accesses
            .into_iter()
            .map(|v| (v.access_id.clone(), v))
            .collect();
        self.market_data_accesses = incoming
            .market_data_accesses
            .into_iter()
            .map(|v| (v.access_id.clone(), v))
            .collect();

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
                for id in $previous.keys() {
                    if !$current.contains_key(id) {
                        events.push(record_event(
                            $kind,
                            concat!($kind, "_removed"),
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
        diff_records!(
            "financial_product",
            previous_financial_products,
            self.financial_products
        );
        diff_records!(
            "execution_access",
            previous_execution_accesses,
            self.execution_accesses
        );
        diff_records!(
            "market_data_access",
            previous_market_data_accesses,
            self.market_data_accesses
        );

        for (id, next) in &next_markets {
            match previous_markets.get(id) {
                None => events.push(LifecycleEvent::listed(
                    next,
                    now,
                    self.event_sequence.get() + events.len() as u64 + 1,
                )),
                Some(previous) if previous != next => {
                    let event_type = if previous.source_symbol != next.source_symbol {
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
                        listing_id: Some(next.listing_id.clone()),
                        exchange_id: Some(next.exchange_id.clone()),
                        source_symbol: Some(next.source_symbol.clone()),
                        previous_status: Some(previous.status),
                        current_status: Some(next.status),
                        previous_symbol: Some(previous.source_symbol.to_string()),
                        current_symbol: Some(next.source_symbol.to_string()),
                        ..LifecycleEvent::default()
                    });
                }
                _ => {}
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
                    listing_id: Some(previous.listing_id.clone()),
                    exchange_id: Some(previous.exchange_id.clone()),
                    source_symbol: Some(previous.source_symbol.clone()),
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
            || previous_financial_products != self.financial_products
            || previous_execution_accesses != self.execution_accesses
        {
            self.generation = Generation::new(self.generation.get().saturating_add(1));
        }
        for event in &mut events {
            event.operation = Some(
                if event.event_type.ends_with("_removed") {
                    "delete"
                } else {
                    "upsert"
                }
                .into(),
            );
            event.generation = self.generation;
            event.record_payload_json = event
                .record_kind
                .as_deref()
                .zip(event.record_id.as_deref())
                .and_then(|(kind, id)| record_payload(self, kind, id));
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

fn record_payload(catalog: &ReferenceCatalog, kind: &str, id: &str) -> Option<String> {
    let value = match kind {
        "entity" => serde_json::to_value(catalog.entities.get(id)?).ok()?,
        "asset" => serde_json::to_value(catalog.assets.get(id)?).ok()?,
        "instrument" => serde_json::to_value(catalog.instruments.get(id)?).ok()?,
        "listing" => serde_json::to_value(catalog.listings.get(id)?).ok()?,
        "market" => serde_json::to_value(catalog.markets.get(id)?).ok()?,
        "financial_product" => serde_json::to_value(catalog.financial_products.get(id)?).ok()?,
        "execution_access" => serde_json::to_value(catalog.execution_accesses.get(id)?).ok()?,
        "market_data_access" => serde_json::to_value(catalog.market_data_accesses.get(id)?).ok()?,
        _ => return None,
    };
    serde_json::to_string(&value).ok()
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::{
        Asset, Entity, FinancialProduct, Instrument, Listing, Market, ProviderCatalog,
        ReferenceCatalog,
    };
    use kairos_domain_types::{Exchange, InstrumentId, ListingId, MarketId, Symbol};

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
            instruments: vec![Default::default()],
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
                market_key: "test.spot.TEST".into(),
                instrument_id: instrument_id("instrument:test"),
                listing_id: listing_id("listing:test"),
                exchange_id: Exchange::new("exchange:test").unwrap(),
                market_type: kairos_domain_types::ProviderProductCode::new("spot").unwrap(),
                source_symbol: kairos_domain_types::Symbol::new("TEST").unwrap(),
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
        assert!(catalog
            .apply(ProviderCatalog::default(), 30.into())
            .is_empty());
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
        assert!(events
            .iter()
            .any(|event| event.event_type == "status_changed"));
        assert_eq!(catalog.markets["market:test"].status, "active".into());
    }

    #[test]
    fn validation_rejects_unresolved_reference_relationships() {
        let catalog = ProviderCatalog {
            assets: vec![Asset {
                asset_id: kairos_domain_types::AssetId::new("asset:BTC").unwrap(),
                code: "BTC".into(),
                asset_class: kairos_domain_types::AssetClass::Crypto,
                status: "active".into(),
                ..Default::default()
            }],
            instruments: vec![Instrument {
                instrument_id: instrument_id("instrument:option"),
                symbol: kairos_domain_types::Symbol::new("BTC-OPT").unwrap(),
                instrument_type: kairos_domain_types::InstrumentKind::Spot,
                status: "active".into(),
                underlying_instrument_id: None,
                ..Default::default()
            }],
            financial_products: vec![FinancialProduct {
                product_id: "product:earn".into(),
                product_type: "earn".into(),
                name: "Earn".into(),
                asset_id: kairos_domain_types::AssetId::new("asset:missing").unwrap(),
                provider_product_id: "earn".into(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let error = catalog.validate().unwrap_err().to_string();
        assert!(error.contains("missing asset"));
    }

    #[test]
    fn validation_rejects_incomplete_option_identity() {
        let error = ProviderCatalog {
            instruments: vec![Instrument {
                instrument_id: instrument_id("instrument:option"),
                symbol: kairos_domain_types::Symbol::new("BTC-OPT").unwrap(),
                instrument_type: kairos_domain_types::InstrumentKind::Option,
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
            listing_id: Some(market.listing_id.clone()),
            exchange_id: Some(market.exchange_id.clone()),
            source_symbol: Some(market.source_symbol.clone()),
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

pub(crate) fn unix_nanos() -> UnixNanos {
    UnixNanos::from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64,
    )
}
