//! Persistence seams owned by the Reference application.

use crate::domain::{LifecycleEvent, ProviderCatalog, ReferenceCatalog, ReferenceResult};

/// Internal persistence seam. The application owns the use case; storage
/// implementations remain selected by composition.
pub(crate) trait CatalogStore: Send {
    fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>>;
    fn save(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<()>;

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

    fn pending_events(&mut self, _limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        Ok(Vec::new())
    }

    fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        Ok(0)
    }

    fn lifecycle_events(
        &mut self,
        _sequence_from: Option<u64>,
        _sequence_to: Option<u64>,
        _limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        Ok(Vec::new())
    }

    fn lifecycle_events_filtered(
        &mut self,
        sequence_from: Option<u64>,
        sequence_to: Option<u64>,
        event_time_from_unix_nanos: Option<u64>,
        event_time_to_unix_nanos: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        let events = self.lifecycle_events(sequence_from, sequence_to, usize::MAX)?;
        Ok(events
            .into_iter()
            .filter(|event| {
                event_time_from_unix_nanos
                    .is_none_or(|from| event.event_time_unix_nanos >= from.into())
                    && event_time_to_unix_nanos
                        .is_none_or(|to| event.event_time_unix_nanos < to.into())
            })
            .take(limit)
            .collect())
    }

    fn acknowledge_pending_events(&mut self, _event_ids: &[String]) -> ReferenceResult<()> {
        Ok(())
    }
}

/// Durable provider cursor state. It is deliberately separate from the
/// business catalog: a page cursor is operational progress, not a reference
/// entity, and must survive a process restart without being published.
pub(crate) trait ProviderSyncStore: Send {
    fn load_state(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Option<(Option<String>, Option<ProviderCatalog>)>>;
    fn save_state(
        &mut self,
        provider: &str,
        cursor: Option<&str>,
        accumulated: Option<&ProviderCatalog>,
    ) -> ReferenceResult<()>;

    fn load_last_good(&mut self, _provider: &str) -> ReferenceResult<Option<ProviderCatalog>> {
        Ok(None)
    }

    fn save_last_good(
        &mut self,
        _provider: &str,
        _catalog: &ProviderCatalog,
    ) -> ReferenceResult<()> {
        Ok(())
    }
}
