//! Single-owner Reference actor.

use crate::application::protocol::{CatalogStore, ReferenceSource};
use crate::domain::{unix_nanos, Asset, LifecycleEvent, ReferenceCatalog, ReferenceResult};

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

    pub fn refresh(&mut self) -> ReferenceResult<RefreshResult> {
        let incoming = self.source.fetch_catalog()?;
        incoming.validate()?;
        let events = self.catalog.apply(incoming, unix_nanos());
        self.store.save(&self.catalog)?;
        Ok(RefreshResult {
            generation: self.catalog.generation,
            event_sequence: self.catalog.event_sequence,
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
}

#[derive(Debug)]
pub struct RefreshResult {
    pub generation: u64,
    pub event_sequence: u64,
    pub events: Vec<LifecycleEvent>,
}
