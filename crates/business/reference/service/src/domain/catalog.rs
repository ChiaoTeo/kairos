//! Reference catalog aggregate and lifecycle reconciliation.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use super::{
    Asset, Entity, ExecutionAccess, FinancialProduct, Instrument, LifecycleEvent, Listing, Market,
    ProviderCatalog,
};

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceCatalog {
    pub entities: BTreeMap<String, Entity>,
    pub assets: BTreeMap<String, Asset>,
    pub instruments: BTreeMap<String, Instrument>,
    pub listings: BTreeMap<String, Listing>,
    pub markets: BTreeMap<String, Market>,
    pub financial_products: BTreeMap<String, FinancialProduct>,
    #[serde(default)]
    pub execution_accesses: BTreeMap<String, ExecutionAccess>,
    pub lifecycle_events: Vec<LifecycleEvent>,
    pub generation: u64,
    pub event_sequence: u64,
}

impl ReferenceCatalog {
    pub fn apply(&mut self, incoming: ProviderCatalog, now: u64) -> Vec<LifecycleEvent> {
        let previous_entities = self.entities.clone();
        let previous_assets = self.assets.clone();
        let previous_instruments = self.instruments.clone();
        let previous_listings = self.listings.clone();
        let previous_markets = self.markets.clone();
        let previous_financial_products = self.financial_products.clone();
        let previous_execution_accesses = self.execution_accesses.clone();
        self.entities = incoming
            .entities
            .into_iter()
            .map(|v| (v.entity_id.clone(), v))
            .collect();
        self.assets = incoming
            .assets
            .into_iter()
            .map(|v| (v.asset_id.clone(), v))
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

        let mut next_markets: BTreeMap<_, _> = incoming
            .markets
            .into_iter()
            .map(|v| (v.market_id.clone(), v))
            .collect();
        let mut events = Vec::new();
        for (id, next) in &next_markets {
            match self.markets.get(id) {
                None => events.push(LifecycleEvent::listed(
                    next,
                    now,
                    self.event_sequence + events.len() as u64 + 1,
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
                            self.event_sequence + events.len() as u64 + 1
                        ),
                        event_type: event_type.to_string(),
                        event_time_unix_nanos: now,
                        market_id: Some(id.clone()),
                        instrument_id: Some(next.instrument_id.clone()),
                        listing_id: Some(next.listing_id.clone()),
                        venue_id: Some(next.venue_id.clone()),
                        source_symbol: Some(next.source_symbol.clone()),
                        previous_status: Some(previous.status.clone()),
                        current_status: Some(next.status.clone()),
                        previous_symbol: Some(previous.source_symbol.clone()),
                        current_symbol: Some(next.source_symbol.clone()),
                    });
                }
                _ => {}
            }
        }
        let mut delisted_records = Vec::new();
        for (id, previous) in &self.markets {
            if !next_markets.contains_key(id) && previous.status != "delisted" {
                let mut delisted = previous.clone();
                delisted.status = "delisted".to_string();
                delisted.effective_to_unix_nanos = Some(now);
                events.push(LifecycleEvent {
                    event_id: format!(
                        "reference:{:020}",
                        self.event_sequence + events.len() as u64 + 1
                    ),
                    event_type: "delisted".to_string(),
                    event_time_unix_nanos: now,
                    market_id: Some(id.clone()),
                    instrument_id: Some(previous.instrument_id.clone()),
                    listing_id: Some(previous.listing_id.clone()),
                    venue_id: Some(previous.venue_id.clone()),
                    source_symbol: Some(previous.source_symbol.clone()),
                    previous_status: Some(previous.status.clone()),
                    current_status: Some("delisted".to_string()),
                    previous_symbol: None,
                    current_symbol: None,
                });
                // Keep the delisted record in the catalog so consumers can resolve it.
                delisted_records.push((id.clone(), delisted));
            }
        }
        for (id, market) in delisted_records {
            next_markets.insert(id, market);
        }
        self.markets = next_markets;
        self.event_sequence += events.len() as u64;
        self.lifecycle_events.extend(events.iter().cloned());
        if previous_entities != self.entities
            || previous_assets != self.assets
            || previous_instruments != self.instruments
            || previous_listings != self.listings
            || previous_markets != self.markets
            || previous_financial_products != self.financial_products
            || previous_execution_accesses != self.execution_accesses
        {
            self.generation += 1;
        }
        events
    }

    pub fn active_market_count(&self) -> usize {
        self.markets
            .values()
            .filter(|market| market.status == "active" || market.status == "trading")
            .count()
    }
}

#[cfg(test)]
mod tests {
    use super::{
        Asset, Entity, FinancialProduct, Instrument, Listing, Market, ProviderCatalog,
        ReferenceCatalog,
    };

    fn catalog_with_market(status: &str) -> ProviderCatalog {
        ProviderCatalog {
            entities: vec![Entity {
                entity_id: "venue:test".into(),
                entity_type: "venue".into(),
                name: "Test Venue".into(),
                status: "active".into(),
            }],
            instruments: vec![Default::default()],
            listings: vec![Listing {
                listing_id: "listing:test".into(),
                instrument_id: "instrument:test".into(),
                venue_id: "venue:test".into(),
                venue_symbol: "TEST".into(),
                status: status.into(),
                effective_from_unix_nanos: 1,
                ..Default::default()
            }],
            markets: vec![Market {
                market_id: "market:test".into(),
                market_key: "test.spot.TEST".into(),
                instrument_id: "instrument:test".into(),
                listing_id: "listing:test".into(),
                venue_id: "venue:test".into(),
                market_type: "spot".into(),
                source_symbol: "TEST".into(),
                status: status.into(),
                effective_from_unix_nanos: 1,
                ..Default::default()
            }],
            ..Default::default()
        }
    }

    #[test]
    fn delisted_market_emits_only_one_delisted_event() {
        let mut catalog = ReferenceCatalog::default();
        assert_eq!(catalog.apply(catalog_with_market("active"), 10).len(), 1);
        assert_eq!(catalog.apply(ProviderCatalog::default(), 20).len(), 1);
        assert!(catalog.apply(ProviderCatalog::default(), 30).is_empty());
        assert_eq!(catalog.lifecycle_events.len(), 2);
    }

    #[test]
    fn delisted_market_can_be_relisted() {
        let mut catalog = ReferenceCatalog::default();
        catalog.apply(catalog_with_market("active"), 10);
        catalog.apply(ProviderCatalog::default(), 20);
        let events = catalog.apply(catalog_with_market("active"), 30);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].event_type, "status_changed");
        assert_eq!(catalog.markets["market:test"].status, "active");
    }

    #[test]
    fn validation_rejects_unresolved_reference_relationships() {
        let catalog = ProviderCatalog {
            assets: vec![Asset {
                asset_id: "asset:btc".into(),
                ..Default::default()
            }],
            instruments: vec![Instrument {
                instrument_id: "instrument:option".into(),
                underlying_instrument_id: Some("instrument:missing".into()),
                ..Default::default()
            }],
            financial_products: vec![FinancialProduct {
                product_id: "product:earn".into(),
                asset_id: "asset:missing".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let error = catalog.validate().unwrap_err().to_string();
        assert!(error.contains("missing asset"));
    }
}

impl LifecycleEvent {
    fn listed(market: &Market, now: u64, sequence: u64) -> Self {
        Self {
            event_id: format!("reference:{sequence:020}"),
            event_type: "listed".to_string(),
            event_time_unix_nanos: now,
            market_id: Some(market.market_id.clone()),
            instrument_id: Some(market.instrument_id.clone()),
            listing_id: Some(market.listing_id.clone()),
            venue_id: Some(market.venue_id.clone()),
            source_symbol: Some(market.source_symbol.clone()),
            current_status: Some(market.status.clone()),
            ..Self::default()
        }
    }
}

pub(crate) fn unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
