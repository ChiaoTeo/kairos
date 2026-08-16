//! Persistence seams owned by the Reference application.

use crate::domain::{LifecycleEvent, ProviderCatalog, ReferenceCatalog, ReferenceResult};

#[derive(Debug)]
pub(crate) struct NormalizedRefresh {
    pub generation: kairos_primitives::Generation,
    pub event_sequence: kairos_primitives::Sequence,
    pub market_count: usize,
    pub changed: bool,
    pub event_count: usize,
}

#[cfg_attr(test, allow(dead_code))]
#[derive(Clone, Copy, Default)]
pub(crate) struct CatalogState {
    pub generation: kairos_primitives::Generation,
    pub event_sequence: kairos_primitives::Sequence,
    pub market_count: usize,
}

/// Internal persistence seam. The application owns the use case; storage
/// implementations remain selected by composition.
pub(crate) trait CatalogStore: Send {
    async fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>>;
    #[cfg_attr(test, allow(dead_code))]
    async fn load_state(&mut self) -> ReferenceResult<CatalogState> {
        let catalog = self.load().await?.unwrap_or_default();
        Ok(CatalogState {
            generation: catalog.generation,
            event_sequence: catalog.event_sequence,
            market_count: catalog.markets.len(),
        })
    }
    /// Atomically persist current state, lifecycle history, and publication
    /// outbox rows. Implementations must not expose a partially committed
    /// refresh if this operation fails.
    async fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
    ) -> ReferenceResult<()>;

    async fn reconcile_provider_facts(
        &mut self,
        _overlay: &ProviderCatalog,
        _now: kairos_primitives::UnixNanos,
    ) -> ReferenceResult<Option<NormalizedRefresh>> {
        Ok(None)
    }

    async fn pending_events(&mut self, _limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        Ok(Vec::new())
    }

    async fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        Ok(0)
    }

    async fn lifecycle_events(
        &mut self,
        _sequence_from: Option<u64>,
        _sequence_to: Option<u64>,
        _limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        Ok(Vec::new())
    }

    async fn lifecycle_events_filtered(
        &mut self,
        sequence_from: Option<u64>,
        sequence_to: Option<u64>,
        event_time_from_unix_nanos: Option<u64>,
        event_time_to_unix_nanos: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        let events = self
            .lifecycle_events(sequence_from, sequence_to, usize::MAX)
            .await?;
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

    async fn acknowledge_pending_events(&mut self, _event_ids: &[String]) -> ReferenceResult<()> {
        Ok(())
    }
}

/// Durable provider cursor state. It is deliberately separate from the
/// business catalog: a page cursor is operational progress, not a reference
/// entity, and must survive a process restart without being published.
pub(crate) trait ProviderSyncStore: Send {
    async fn load_state(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Option<(Option<String>, Option<ProviderCatalog>)>>;
    #[allow(dead_code)] // retained only to migrate a pre-staging cursor payload
    async fn save_state(
        &mut self,
        provider: &str,
        cursor: Option<&str>,
        accumulated: Option<&ProviderCatalog>,
    ) -> ReferenceResult<()>;

    async fn load_last_good(
        &mut self,
        _provider: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        Ok(None)
    }

    async fn has_last_good(&mut self, provider: &str) -> ReferenceResult<bool> {
        Ok(self.load_last_good(provider).await?.is_some())
    }

    async fn save_last_good(
        &mut self,
        _provider: &str,
        _catalog: &ProviderCatalog,
    ) -> ReferenceResult<()> {
        Ok(())
    }

    /// Append one normalized provider page and advance its durable cursor in
    /// the same transaction. Pages are staging data, never visible catalog
    /// state; this keeps a long provider scan restartable without retaining
    /// the complete candidate in process memory.
    async fn append_staged_page(
        &mut self,
        _provider: &str,
        _cursor: Option<&str>,
        _catalog: &ProviderCatalog,
    ) -> ReferenceResult<()> {
        Ok(())
    }

    async fn staged_pages(&mut self, _provider: &str) -> ReferenceResult<Vec<ProviderCatalog>> {
        Ok(Vec::new())
    }

    async fn clear_staged_pages(&mut self, _provider: &str) -> ReferenceResult<()> {
        Ok(())
    }

    fn supports_normalized_promotion(&self) -> bool {
        false
    }

    /// Marks one completed staged scan for Actor-controlled promotion. The
    /// CatalogStore consumes it in the same transaction as canonical rows,
    /// lifecycle rows and watermarks.
    async fn promote_staged(&mut self, _provider: &str) -> ReferenceResult<()> {
        Err(crate::domain::ReferenceError::Persistence(
            "normalized provider promotion is not supported by this store".into(),
        ))
    }

    async fn remove_last_good(&mut self, _provider: &str) -> ReferenceResult<()> {
        Ok(())
    }

    async fn paused_sources(&mut self) -> ReferenceResult<Vec<String>> {
        Ok(Vec::new())
    }

    async fn set_source_paused(&mut self, _provider: &str, _paused: bool) -> ReferenceResult<()> {
        Ok(())
    }

    /// Explicit options coverage is operational configuration owned by
    /// Reference. It is separate from the published catalog because coverage
    /// says what to discover, not what has already been discovered.
    async fn option_underlyings(&mut self, _provider: &str) -> ReferenceResult<Vec<String>> {
        Ok(Vec::new())
    }

    async fn set_option_underlying(
        &mut self,
        _provider: &str,
        _underlying: &str,
        _enabled: bool,
    ) -> ReferenceResult<()> {
        Ok(())
    }
}

impl ProviderSyncStore for () {
    async fn load_state(
        &mut self,
        _provider: &str,
    ) -> ReferenceResult<Option<(Option<String>, Option<ProviderCatalog>)>> {
        Ok(None)
    }

    async fn save_state(
        &mut self,
        _provider: &str,
        _cursor: Option<&str>,
        _accumulated: Option<&ProviderCatalog>,
    ) -> ReferenceResult<()> {
        Ok(())
    }
}
