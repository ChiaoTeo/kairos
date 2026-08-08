//! Reference domain catalog and lifecycle reconciliation.

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

pub type ReferenceResult<T> = Result<T, ReferenceError>;

#[derive(Debug)]
pub enum ReferenceError {
    Invalid(String),
    Provider(String),
    Persistence(String),
    Publication(String),
}

impl std::fmt::Display for ReferenceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value) => write!(f, "invalid reference data: {value}"),
            Self::Provider(value) => write!(f, "reference provider failed: {value}"),
            Self::Persistence(value) => write!(f, "reference persistence failed: {value}"),
            Self::Publication(value) => write!(f, "reference publication failed: {value}"),
        }
    }
}

impl std::error::Error for ReferenceError {}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Entity {
    pub entity_id: String,
    pub entity_type: String,
    pub name: String,
    pub status: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Asset {
    pub asset_id: String,
    pub code: String,
    pub name: Option<String>,
    pub asset_class: String,
    pub status: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Instrument {
    pub instrument_id: String,
    pub symbol: String,
    pub name: Option<String>,
    pub instrument_type: String,
    pub product_family: Option<String>,
    pub underlying_instrument_id: Option<String>,
    pub expiry_unix_nanos: Option<u64>,
    pub strike: Option<String>,
    pub option_right: Option<String>,
    pub status: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Listing {
    pub listing_id: String,
    pub instrument_id: String,
    pub venue_id: String,
    pub venue_symbol: String,
    pub status: String,
    pub effective_from_unix_nanos: u64,
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Market {
    pub market_id: String,
    pub market_key: String,
    pub instrument_id: String,
    pub listing_id: String,
    pub venue_id: String,
    pub market_type: String,
    #[serde(default)]
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
    pub effective_from_unix_nanos: u64,
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct LifecycleEvent {
    pub event_id: String,
    pub event_type: String,
    pub event_time_unix_nanos: u64,
    pub market_id: Option<String>,
    pub instrument_id: Option<String>,
    pub listing_id: Option<String>,
    pub venue_id: Option<String>,
    pub source_symbol: Option<String>,
    pub previous_status: Option<String>,
    pub current_status: Option<String>,
    pub previous_symbol: Option<String>,
    pub current_symbol: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct FinancialProduct {
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

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProviderCatalog {
    pub entities: Vec<Entity>,
    pub assets: Vec<Asset>,
    pub instruments: Vec<Instrument>,
    pub listings: Vec<Listing>,
    pub markets: Vec<Market>,
    pub financial_products: Vec<FinancialProduct>,
}

impl ProviderCatalog {
    /// Validate the provider boundary before any actor-owned state is changed.
    pub fn validate(&self) -> ReferenceResult<()> {
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
            if !instrument_ids.contains(listing.instrument_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "listing {} references missing instrument {}",
                    listing.listing_id, listing.instrument_id
                )));
            }
            if !entity_ids.contains(listing.venue_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "listing {} references missing venue {}",
                    listing.listing_id, listing.venue_id
                )));
            }
        }
        for market in &self.markets {
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
            if !entity_ids.contains(market.venue_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "market {} references missing venue {}",
                    market.market_id, market.venue_id
                )));
            }
        }
        for product in &self.financial_products {
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

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceCatalog {
    pub entities: BTreeMap<String, Entity>,
    pub assets: BTreeMap<String, Asset>,
    pub instruments: BTreeMap<String, Instrument>,
    pub listings: BTreeMap<String, Listing>,
    pub markets: BTreeMap<String, Market>,
    pub financial_products: BTreeMap<String, FinancialProduct>,
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
