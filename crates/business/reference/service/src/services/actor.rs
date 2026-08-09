//! Single-owner Reference actor.

use super::providers::ReferenceSource;
use super::storage::CatalogStore;
use crate::domain::{
    unix_nanos, Asset, LifecycleEvent, ProviderHealth, ReferenceCatalog, ReferenceResult,
};
use tracing::info;

pub struct ReferenceActor {
    pub actor_id: String,
    pub catalog: ReferenceCatalog,
    source: Box<dyn ReferenceSource>,
    store: Box<dyn CatalogStore>,
}

impl ReferenceActor {
    pub fn new(
        actor_id: impl Into<String>,
        source: Box<dyn ReferenceSource>,
        mut store: Box<dyn CatalogStore>,
    ) -> ReferenceResult<Self> {
        let catalog = store.load()?.unwrap_or_default();
        info!(
            event = "reference_state_loaded",
            component = "reference",
            generation = catalog.generation,
            event_sequence = catalog.event_sequence,
            asset_count = catalog.assets.len(),
            market_count = catalog.markets.len(),
            "reference catalog loaded from persistence"
        );
        Ok(Self {
            actor_id: actor_id.into(),
            catalog,
            source,
            store,
        })
    }

    pub fn source_id(&self) -> &str {
        self.source.source_id()
    }

    pub fn provider_health(&self) -> Vec<ProviderHealth> {
        self.source.provider_health()
    }

    pub fn refresh(&mut self) -> ReferenceResult<RefreshResult> {
        let incoming = self.source.fetch_catalog()?;
        incoming.validate()?;
        let previous_generation = self.catalog.generation;
        let events = self.catalog.apply(incoming, unix_nanos());
        const IN_MEMORY_LIFECYCLE_LIMIT: usize = 4096;
        if self.catalog.lifecycle_events.len() > IN_MEMORY_LIFECYCLE_LIMIT {
            let keep_from = self.catalog.lifecycle_events.len() - IN_MEMORY_LIFECYCLE_LIMIT;
            self.catalog.lifecycle_events.drain(..keep_from);
        }
        let changed = previous_generation != self.catalog.generation || !events.is_empty();
        if changed {
            self.store.save_refresh(&self.catalog, &events)?;
        }
        Ok(RefreshResult {
            generation: self.catalog.generation,
            event_sequence: self.catalog.event_sequence,
            changed,
            events,
        })
    }

    pub fn upsert_asset(&mut self, asset: Asset) -> ReferenceResult<()> {
        if asset.asset_id.trim().is_empty() || asset.code.trim().is_empty() {
            return Err(crate::domain::ReferenceError::Invalid(
                "asset_id and code are required".to_owned(),
            ));
        }
        self.catalog.assets.insert(asset.asset_id.clone(), asset);
        self.catalog.generation = self.catalog.generation.saturating_add(1);
        self.store.save(&self.catalog)?;
        Ok(())
    }

    pub fn pending_events(&mut self, limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.store.pending_events(limit)
    }

    pub fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        self.store.pending_event_count()
    }

    pub fn lifecycle_events(
        &mut self,
        sequence_from: Option<u64>,
        sequence_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        let events = self
            .store
            .lifecycle_events(sequence_from, sequence_to, limit)?;
        if events.is_empty() && !self.catalog.lifecycle_events.is_empty() {
            return Ok(self
                .catalog
                .lifecycle_events
                .iter()
                .filter(|event| {
                    let sequence = event
                        .event_id
                        .rsplit(':')
                        .next()
                        .and_then(|value| value.parse::<u64>().ok())
                        .unwrap_or(0);
                    sequence_from.is_none_or(|value| sequence >= value)
                        && sequence_to.is_none_or(|value| sequence <= value)
                })
                .take(limit)
                .cloned()
                .collect());
        }
        Ok(events)
    }

    pub fn acknowledge_pending_events(&mut self, event_ids: &[String]) -> ReferenceResult<()> {
        self.store.acknowledge_pending_events(event_ids)
    }
}

#[derive(Debug)]
pub struct RefreshResult {
    pub generation: u64,
    pub event_sequence: u64,
    pub changed: bool,
    pub events: Vec<LifecycleEvent>,
}
