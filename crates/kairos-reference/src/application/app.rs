//! Public reference use-case facade.
//!
//! The facade owns the reference actor but does not expose it.  Callers use
//! refresh/publish commands or read-only market queries; persistence,
//! provider connections and publication transports stay behind private services.

use crate::domain::LifecycleEvent;
use crate::domain::{Asset, Market, ReferenceError, ReferenceResult};

use crate::application::protocol::{CatalogStore, ReferenceSource};
use crate::application::queries::{
    LifecycleQuery, MarketQuery, ReferenceKind, ReferenceQuery, ReferenceReader, ReferenceRecord,
};
use crate::services::actor::ReferenceActor;

/// Public application boundary for reference data.
pub struct ReferenceApplication {
    actor: ReferenceActor,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceRefreshResult {
    pub generation: u64,
    pub event_sequence: u64,
    pub events: Vec<LifecycleEvent>,
}

impl ReferenceApplication {
    pub fn new(
        actor_id: impl Into<String>,
        source: Box<dyn ReferenceSource>,
        store: Box<dyn CatalogStore>,
    ) -> ReferenceResult<Self> {
        Ok(Self {
            actor: ReferenceActor::new(actor_id, source, store)?,
        })
    }

    pub fn actor_id(&self) -> &str {
        &self.actor.actor_id
    }

    pub fn source_id(&self) -> &str {
        self.actor.source_id()
    }

    /// Refresh provider data, reconcile lifecycle changes, persist and publish.
    pub fn refresh(&mut self) -> ReferenceResult<ReferenceRefreshResult> {
        let result = self.actor.refresh()?;
        Ok(ReferenceRefreshResult {
            generation: result.generation,
            event_sequence: result.event_sequence,
            events: result.events,
        })
    }

    pub fn upsert_asset(&mut self, asset: Asset) -> ReferenceResult<u64> {
        self.actor.upsert_asset(asset)?;
        Ok(self.actor.catalog.generation)
    }

    pub fn pending_events(&mut self) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.actor.pending_events()
    }

    /// Read the append-only lifecycle history by stable sequence and time.
    pub fn lifecycle_events(&self, query: &LifecycleQuery) -> Vec<LifecycleEvent> {
        let mut events = self
            .actor
            .catalog
            .lifecycle_events
            .iter()
            .enumerate()
            .filter(|(index, event)| query.matches(*index as u64 + 1, event))
            .map(|(_, event)| event.clone())
            .collect::<Vec<_>>();
        if let Some(limit) = query.limit {
            events.truncate(limit);
        }
        events
    }

    /// Replay lifecycle events in their persisted sequence order.
    pub fn replay_lifecycle_events(
        &self,
        sequence_from: Option<u64>,
        sequence_to: Option<u64>,
    ) -> Vec<LifecycleEvent> {
        self.lifecycle_events(&LifecycleQuery {
            sequence_from,
            sequence_to,
            ..LifecycleQuery::default()
        })
    }

    pub fn acknowledge_published_events(&mut self) -> ReferenceResult<()> {
        self.actor.acknowledge_pending_events()
    }

    /// Read the current catalog for diagnostics and controlled projections.
    ///
    /// The returned reference is read-only; mutation remains owned by this
    /// application instance and its actor.
    pub fn catalog(&self) -> &crate::domain::ReferenceCatalog {
        &self.actor.catalog
    }

    pub fn markets(&self, query: &MarketQuery) -> Vec<Market> {
        self.actor
            .catalog
            .markets
            .values()
            .filter(|market| query.matches(market))
            .cloned()
            .collect()
    }

    pub fn resolve_market(&self, query: &MarketQuery) -> ReferenceResult<Market> {
        let markets = self.markets(query);
        match markets.as_slice() {
            [market] => Ok(market.clone()),
            [] => Err(ReferenceError::Invalid(query.not_found_message())),
            _ => Err(ReferenceError::Invalid(query.ambiguous_message())),
        }
    }

    /// Execute the complete read-side catalog query used by the verification
    /// CLI. The application owns filtering so server and CLI cannot drift.
    pub fn query(&self, query: &ReferenceQuery) -> Vec<ReferenceRecord> {
        let mut records = Vec::new();
        let include = |kind: ReferenceKind| query.kind == ReferenceKind::All || query.kind == kind;
        if include(ReferenceKind::Entity) {
            records.extend(
                self.actor
                    .catalog
                    .entities
                    .values()
                    .filter(|value| {
                        query.matches_status(&value.status)
                            && query.matches_text(&[
                                &value.entity_id,
                                &value.entity_type,
                                &value.name,
                            ])
                            && query
                                .venue_id
                                .as_deref()
                                .is_none_or(|venue| venue == value.entity_id)
                    })
                    .cloned()
                    .map(ReferenceRecord::Entity),
            );
        }
        if include(ReferenceKind::Asset) {
            records.extend(
                self.actor
                    .catalog
                    .assets
                    .values()
                    .filter(|value| {
                        query.matches_status(&value.status)
                            && query.matches_text(&[
                                &value.asset_id,
                                &value.code,
                                value.name.as_deref().unwrap_or(""),
                            ])
                    })
                    .cloned()
                    .map(ReferenceRecord::Asset),
            );
        }
        if include(ReferenceKind::Instrument) {
            records.extend(
                self.actor
                    .catalog
                    .instruments
                    .values()
                    .filter(|value| {
                        query.matches_status(&value.status)
                            && query.matches_text(&[
                                &value.instrument_id,
                                &value.symbol,
                                value.name.as_deref().unwrap_or(""),
                                value.instrument_type.as_str(),
                            ])
                            && query.underlying_instrument_id.as_deref().is_none_or(|id| {
                                value.underlying_instrument_id.as_deref() == Some(id)
                            })
                    })
                    .cloned()
                    .map(ReferenceRecord::Instrument),
            );
        }
        if include(ReferenceKind::Listing) {
            records.extend(
                self.actor
                    .catalog
                    .listings
                    .values()
                    .filter(|value| {
                        query.matches_status(&value.status)
                            && query
                                .venue_id
                                .as_deref()
                                .is_none_or(|venue| venue == value.venue_id)
                            && query.as_of_unix_nanos.is_none_or(|at| {
                                value.effective_from_unix_nanos <= at
                                    && value.effective_to_unix_nanos.is_none_or(|end| at < end)
                            })
                            && query.matches_text(&[
                                &value.listing_id,
                                &value.instrument_id,
                                &value.venue_symbol,
                                &value.venue_id,
                            ])
                    })
                    .cloned()
                    .map(ReferenceRecord::Listing),
            );
        }
        if include(ReferenceKind::Market) {
            let market_query = MarketQuery {
                venue_id: query.venue_id.clone(),
                market_type: query.market_type.clone(),
                asset_type: query.asset_type.clone(),
                source_symbol: query.text.clone(),
                active_only: query.active_only,
                as_of_unix_nanos: query.as_of_unix_nanos,
                status: query.status.clone(),
                ..MarketQuery::default()
            };
            records.extend(
                self.markets(&market_query)
                    .into_iter()
                    .filter(|market| {
                        query
                            .underlying_instrument_id
                            .as_deref()
                            .is_none_or(|underlying| {
                                self.actor
                                    .catalog
                                    .markets
                                    .get(&market.market_id)
                                    .and_then(|value| value.underlying_instrument_id.as_deref())
                                    == Some(underlying)
                            })
                    })
                    .map(ReferenceRecord::Market),
            );
        }
        if include(ReferenceKind::FinancialProduct) {
            records.extend(
                self.actor
                    .catalog
                    .financial_products
                    .values()
                    .filter(|value| {
                        query.matches_status(&value.status)
                            && query.matches_text(&[
                                &value.product_id,
                                &value.provider_product_id,
                                &value.product_type,
                                &value.name,
                            ])
                    })
                    .cloned()
                    .map(ReferenceRecord::FinancialProduct),
            );
        }
        if include(ReferenceKind::Event) {
            let lifecycle_query = LifecycleQuery {
                sequence_from: query.sequence_from,
                sequence_to: query.sequence_to,
                venue_id: query.venue_id.clone(),
                event_time_from_unix_nanos: query.event_time_from_unix_nanos,
                event_time_to_unix_nanos: query.event_time_to_unix_nanos,
                limit: None,
                ..LifecycleQuery::default()
            };
            records.extend(
                self.lifecycle_events(&lifecycle_query)
                    .into_iter()
                    .filter(|value| {
                        query.matches_status(value.current_status.as_deref().unwrap_or(""))
                            && query.matches_text(&[
                                &value.event_id,
                                &value.event_type,
                                value.market_id.as_deref().unwrap_or(""),
                                value.source_symbol.as_deref().unwrap_or(""),
                            ])
                    })
                    .map(ReferenceRecord::Event),
            );
        }
        if let Some(limit) = query.limit {
            records.truncate(limit);
        }
        records
    }

    pub fn record(&self, identifier: &str) -> ReferenceResult<ReferenceRecord> {
        let mut matches = Vec::new();
        if let Some(value) = self.actor.catalog.entities.get(identifier) {
            matches.push(ReferenceRecord::Entity(value.clone()));
        }
        if let Some(value) = self.actor.catalog.assets.get(identifier) {
            matches.push(ReferenceRecord::Asset(value.clone()));
        }
        if let Some(value) = self.actor.catalog.instruments.get(identifier) {
            matches.push(ReferenceRecord::Instrument(value.clone()));
        }
        if let Some(value) = self.actor.catalog.listings.get(identifier) {
            matches.push(ReferenceRecord::Listing(value.clone()));
        }
        if let Some(value) = self.actor.catalog.markets.get(identifier) {
            matches.push(ReferenceRecord::Market(value.clone()));
        }
        if let Some(value) = self.actor.catalog.financial_products.get(identifier) {
            matches.push(ReferenceRecord::FinancialProduct(value.clone()));
        }
        matches.extend(
            self.actor
                .catalog
                .lifecycle_events
                .iter()
                .filter(|value| value.event_id == identifier)
                .cloned()
                .map(ReferenceRecord::Event),
        );
        match matches.as_slice() {
            [record] => Ok(record.clone()),
            [] => Err(crate::domain::ReferenceError::Invalid(format!(
                "unknown reference identifier: {identifier}"
            ))),
            _ => Err(crate::domain::ReferenceError::Invalid(format!(
                "reference identifier is ambiguous: {identifier}"
            ))),
        }
    }
}

impl ReferenceReader for ReferenceApplication {
    fn markets(&self, query: &MarketQuery) -> Vec<Market> {
        self.markets(query)
    }

    fn resolve_market(&self, query: &MarketQuery) -> ReferenceResult<Market> {
        self.resolve_market(query)
    }
}
