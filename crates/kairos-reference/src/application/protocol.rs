//! Ports owned by the reference application.

pub use crate::domain::{LifecycleEvent, ProviderCatalog, ReferenceCatalog, ReferenceResult};

pub trait ReferenceSource: Send {
    fn source_id(&self) -> &str;
    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog>;
}

pub trait CatalogStore: Send {
    fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>>;
    fn save(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<()>;

    /// Persist a catalog mutation and its newly generated events as one store
    /// operation. Durable stores should override this to make the snapshot
    /// and publication outbox atomic.
    fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
    ) -> ReferenceResult<()> {
        self.save(catalog)?;
        self.enqueue_events(events)
    }

    fn enqueue_events(&mut self, _events: &[LifecycleEvent]) -> ReferenceResult<()> {
        Ok(())
    }

    fn pending_events(&mut self) -> ReferenceResult<Vec<LifecycleEvent>> {
        Ok(Vec::new())
    }

    fn acknowledge_pending_events(&mut self) -> ReferenceResult<()> {
        Ok(())
    }
}

#[derive(Clone, Debug, Default)]
pub struct PublishedSnapshots {
    pub catalog: Vec<u8>,
    pub markets: Vec<u8>,
    pub lifecycle: Vec<u8>,
}
