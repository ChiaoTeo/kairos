//! Single-owner Reference actor.

use kairos_primitives::runtime::{ActorId, InstanceIdentity};
use tracing::info;

use super::providers::ReferenceSourcePlan;
use super::publication::{EncodedPublication, encode_publications};
#[cfg(not(test))]
use super::sources::ConfiguredReferenceSource;
use super::sources::ReferenceSource;
use super::storage::catalog_store::{CatalogReconcileSummary, SqlxCatalogStore};
use super::storage::provider_sync_store::SqlxProviderSyncStore;
use super::storage::publication_outbox_store::SqlxPublicationOutbox;
use super::time::unix_nanos;
use crate::domain::{
    AffectedReferenceSet, Asset, Instrument, LifecycleEvent, Listing, ManualUpsertPolicy, Market,
    ProviderCatalog, ReferenceCatalog, ReferenceResult, ReferenceSourceDefinition,
    SourceDesiredState, SourceHealth, SourceTickBudget,
};
use crate::logging::events as log_events;

#[cfg(not(test))]
type ActorReferenceSource = ConfiguredReferenceSource;
#[cfg(test)]
type ActorReferenceSource = Box<dyn ReferenceSource>;

pub struct ReferenceActor {
    pub actor_id: ActorId,
    pub metadata: CatalogMetadata,
    #[cfg(test)]
    pub catalog: ReferenceCatalog,
    source: Option<ActorReferenceSource>,
    source_plan: Option<ReferenceSourcePlan>,
    store: SqlxCatalogStore,
    provider_sync_store: SqlxProviderSyncStore,
    publication_outbox: SqlxPublicationOutbox,
    producer_incarnation: u64,
    identity: InstanceIdentity,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct CatalogMetadata {
    pub generation: kairos_primitives::time::Generation,
    pub event_sequence: kairos_primitives::time::Sequence,
    pub committed_at_unix_nanos: kairos_primitives::time::UnixNanos,
    pub exchange_count: usize,
    pub asset_count: usize,
    pub instrument_count: usize,
    pub listing_count: usize,
    pub market_count: usize,
    pub active_market_count: usize,
    pub lifecycle_event_count: usize,
    pub missing_equity_market_count: usize,
    pub legacy_exchange_market_id_count: usize,
    pub legacy_exchange_listing_id_count: usize,
    pub option_listing_count: usize,
    pub option_market_count: usize,
}

impl From<&ReferenceCatalog> for CatalogMetadata {
    fn from(catalog: &ReferenceCatalog) -> Self {
        Self {
            generation: catalog.generation,
            event_sequence: catalog.event_sequence,
            committed_at_unix_nanos: kairos_primitives::time::UnixNanos::from(0),
            exchange_count: catalog.exchanges.len(),
            asset_count: catalog.assets.len(),
            instrument_count: catalog.instruments.len(),
            listing_count: catalog.listings.len(),
            market_count: catalog.markets.len(),
            active_market_count: catalog
                .markets
                .values()
                .filter(|market| market_is_active(market))
                .count(),
            lifecycle_event_count: catalog.lifecycle_events.len(),
            missing_equity_market_count: 0,
            legacy_exchange_market_id_count: catalog
                .markets
                .keys()
                .filter(|market_id| market_id.as_str().starts_with("market:exchange:"))
                .count(),
            legacy_exchange_listing_id_count: catalog
                .listings
                .keys()
                .filter(|listing_id| listing_id.as_str().starts_with("listing:exchange:"))
                .count(),
            option_listing_count: catalog
                .listings
                .keys()
                .filter(|listing_id| listing_id.as_str().contains(":option:"))
                .count(),
            option_market_count: catalog
                .markets
                .values()
                .filter(|market| market.instrument_kind.as_str() == "option")
                .count(),
        }
    }
}

impl From<super::storage::catalog_store::CatalogRuntimeSnapshot> for CatalogMetadata {
    fn from(state: super::storage::catalog_store::CatalogRuntimeSnapshot) -> Self {
        Self {
            generation: state.generation,
            event_sequence: state.event_sequence,
            committed_at_unix_nanos: state.committed_at_unix_nanos,
            exchange_count: state.exchange_count,
            asset_count: state.asset_count,
            instrument_count: state.instrument_count,
            listing_count: state.listing_count,
            market_count: state.market_count,
            active_market_count: state.active_market_count,
            lifecycle_event_count: state.lifecycle_event_count,
            missing_equity_market_count: state.missing_equity_market_count,
            legacy_exchange_market_id_count: state.legacy_exchange_market_id_count,
            legacy_exchange_listing_id_count: state.legacy_exchange_listing_id_count,
            option_listing_count: state.option_listing_count,
            option_market_count: state.option_market_count,
        }
    }
}

fn market_is_active(market: &Market) -> bool {
    matches!(market.status.as_str(), "active" | "trading")
}

fn log_catalog_loaded(metadata: &CatalogMetadata) {
    let log_event = log_events::STARTUP_STAGE_COMPLETED;
    info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_state_loaded",
        generation = metadata.generation.get(),
        event_sequence = metadata.event_sequence.get(),
        committed_at_unix_nanos = metadata.committed_at_unix_nanos.get(),
        exchange_count = metadata.exchange_count,
        asset_count = metadata.asset_count,
        instrument_count = metadata.instrument_count,
        listing_count = metadata.listing_count,
        market_count = metadata.market_count,
        active_market_count = metadata.active_market_count,
        lifecycle_event_count = metadata.lifecycle_event_count,
        missing_equity_market_count = metadata.missing_equity_market_count,
        legacy_exchange_market_id_count = metadata.legacy_exchange_market_id_count,
        legacy_exchange_listing_id_count = metadata.legacy_exchange_listing_id_count,
        option_listing_count = metadata.option_listing_count,
        option_market_count = metadata.option_market_count,
        "reference catalog loaded from persistence"
    );
}

fn log_reconcile_apply_completed(
    metadata: &CatalogMetadata,
    changed: bool,
    event_count: usize,
    affected_summary: &CatalogReconcileSummary,
    duration_ms: u64,
) {
    let log_event = log_events::RECONCILE_APPLY_COMPLETED;
    info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_reconcile_completed",
        generation = metadata.generation.get(),
        event_sequence = metadata.event_sequence.get(),
        changed,
        event_count,
        affected_write_mode = affected_summary.affected_write_mode,
        affected_total_count = affected_summary.affected_total_count,
        affected_exchange_count = affected_summary.affected_exchange_count,
        affected_asset_count = affected_summary.affected_asset_count,
        affected_instrument_count = affected_summary.affected_instrument_count,
        affected_listing_count = affected_summary.affected_listing_count,
        affected_market_count = affected_summary.affected_market_count,
        duration_ms,
        "reference reconcile completed"
    );
}

impl ReferenceActor {
    pub async fn new(
        actor_id: impl Into<String>,
        workspace_id: impl Into<String>,
        source_plan: ReferenceSourcePlan,
        mut store: SqlxCatalogStore,
    ) -> ReferenceResult<Self> {
        #[cfg(test)]
        let catalog = store.load().await?.unwrap_or_default();
        #[cfg(test)]
        let metadata = CatalogMetadata::from(&catalog);
        #[cfg(not(test))]
        let metadata = {
            let state = store.load_runtime_snapshot().await?;
            CatalogMetadata::from(state)
        };
        log_catalog_loaded(&metadata);
        Ok(Self {
            actor_id: ActorId::new(actor_id.into())?,
            metadata,
            #[cfg(test)]
            catalog,
            source: None,
            source_plan: Some(source_plan),
            provider_sync_store: SqlxProviderSyncStore::from_pool(store.pool.clone()),
            publication_outbox: SqlxPublicationOutbox::from_pool(store.pool.clone()),
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
            identity: InstanceIdentity::unscoped(workspace_id)?,
            store,
        })
    }

    #[cfg(test)]
    pub async fn new_test<S>(
        actor_id: impl Into<String>,
        source: S,
        mut store: SqlxCatalogStore,
    ) -> ReferenceResult<Self>
    where
        S: ReferenceSource + 'static,
    {
        let catalog = store.load().await?.unwrap_or_default();
        let metadata = CatalogMetadata::from(&catalog);
        Ok(Self {
            actor_id: ActorId::new(actor_id.into())?,
            metadata,
            catalog,
            source: Some(Box::new(source)),
            source_plan: None,
            provider_sync_store: SqlxProviderSyncStore::from_pool(store.pool.clone()),
            publication_outbox: SqlxPublicationOutbox::from_pool(store.pool.clone()),
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
            identity: InstanceIdentity::unscoped("workspace:test")?,
            store,
        })
    }

    pub fn source_id(&self) -> &str {
        self.source
            .as_ref()
            .map(|source| source.source_id())
            .unwrap_or("reference-providers")
    }

    pub fn source_health(&self) -> Vec<SourceHealth> {
        self.source
            .as_ref()
            .map(|source| source.source_health())
            .unwrap_or_default()
    }

    pub async fn activate_sources(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        if self.source.is_some() {
            return Ok(());
        }
        let plan = self.source_plan.take().ok_or_else(|| {
            crate::domain::ReferenceError::Provider("Reference source plan is unavailable".into())
        })?;
        let source = plan.activate(connections).await?;
        #[cfg(not(test))]
        {
            self.source = Some(source);
        }
        #[cfg(test)]
        {
            self.source = Some(Box::new(source));
        }
        Ok(())
    }

    fn source_mut(&mut self) -> ReferenceResult<&mut ActorReferenceSource> {
        self.source.as_mut().ok_or_else(|| {
            crate::domain::ReferenceError::Provider("Reference sources are not active".into())
        })
    }

    pub async fn advance_sources_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<RefreshResult> {
        let started = std::time::Instant::now();
        let normalized = self.source_mut()?.normalized_facts_authoritative();
        let incoming = self
            .source_mut()?
            .advance_workflow_with_budget(connections, budget)
            .await?;
        if normalized {
            return self.reconcile_normalized(incoming, started).await;
        }
        self.reconcile(incoming, started).await
    }

    #[cfg(test)]
    pub async fn refresh(&mut self) -> ReferenceResult<RefreshResult> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        self.advance_sources_with_connections(
            &mut system.connections(),
            SourceTickBudget::default(),
        )
        .await
    }

    pub async fn advance_source_with_connections(
        &mut self,
        source_id: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<RefreshResult> {
        let started = std::time::Instant::now();
        let normalized = self.source_mut()?.normalized_facts_authoritative();
        match self
            .source_mut()?
            .advance_source_with_budget(source_id, connections, budget)
            .await?
        {
            Some(incoming) if normalized => self.reconcile_normalized(incoming, started).await,
            Some(incoming) => self.reconcile(incoming, started).await,
            None => Ok(RefreshResult {
                generation: self.metadata.generation,
                event_sequence: self.metadata.event_sequence,
                changed: false,
                event_count: 0,
                events: Vec::new(),
            }),
        }
    }

    async fn reconcile_normalized(
        &mut self,
        overlay: ProviderCatalog,
        started: std::time::Instant,
    ) -> ReferenceResult<RefreshResult> {
        overlay.validate()?;
        let candidate = self
            .provider_sync_store
            .load_provider_candidate(&overlay)
            .await?;
        self.reconcile_candidate(candidate, started, true).await
    }

    pub async fn set_source_desired_state(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
    ) -> ReferenceResult<()> {
        self.source_mut()?
            .set_source_desired_state(source_id, desired_state)
            .await
    }

    pub async fn set_source_desired_state_with_connections(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.source_mut()?
            .set_source_desired_state_with_connections(source_id, desired_state, connections)
            .await
    }

    pub async fn upsert_source_definition(
        &mut self,
        definition: ReferenceSourceDefinition,
    ) -> ReferenceResult<()> {
        self.source_mut()?
            .upsert_source_definition(definition)
            .await
    }

    pub async fn upsert_source_definition_with_connections(
        &mut self,
        definition: ReferenceSourceDefinition,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.source_mut()?
            .upsert_source_definition_with_connections(definition, connections)
            .await
    }

    pub fn option_underlyings(&self) -> Vec<String> {
        self.source
            .as_ref()
            .map(|source| source.option_underlyings())
            .unwrap_or_default()
    }

    /// Change scoped source coverage and immediately advance that source.
    /// Additions may require more pages and therefore legitimately return an
    /// unchanged catalog until the scoped candidate is complete; removals are
    /// reconciled immediately from the remaining committed scopes.
    #[cfg(test)]
    pub async fn set_source_scope(
        &mut self,
        source_id: &str,
        scope: crate::domain::SourceScope,
        enabled: bool,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<RefreshResult> {
        self.source_mut()?
            .set_source_scope_with_connections(source_id, scope, enabled, connections)
            .await?;
        self.advance_source_with_connections(source_id, connections, SourceTickBudget::default())
            .await
    }

    #[cfg(not(test))]
    pub fn massive_option_connection_plan(
        &self,
        underlying: &str,
    ) -> ReferenceResult<(
        kairos_conflux::ConnectionKey,
        kairos_conflux::MassiveRestConfig,
    )> {
        self.source
            .as_ref()
            .ok_or_else(|| {
                crate::domain::ReferenceError::Provider(
                    "Reference provider connections have not been activated".into(),
                )
            })?
            .massive_option_connection_plan(underlying)
    }

    #[cfg(not(test))]
    pub async fn set_managed_source_scope(
        &mut self,
        source_id: &str,
        scope: crate::domain::SourceScope,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<RefreshResult> {
        self.source_mut()?
            .set_managed_source_scope(source_id, scope, enabled, connection_key)
            .await?;
        self.advance_source_with_connections(source_id, connections, SourceTickBudget::default())
            .await
    }

    async fn reconcile(
        &mut self,
        incoming: ProviderCatalog,
        started: std::time::Instant,
    ) -> ReferenceResult<RefreshResult> {
        self.reconcile_candidate(incoming, started, false).await
    }

    async fn reconcile_candidate(
        &mut self,
        incoming: ProviderCatalog,
        started: std::time::Instant,
        commit_provider_promotions: bool,
    ) -> ReferenceResult<RefreshResult> {
        incoming.validate()?;
        let mut candidate = self.store.load().await?.unwrap_or_default();
        let previous_generation = candidate.generation;
        let events = candidate.apply(incoming, unix_nanos());
        let affected = AffectedReferenceSet::from_events(&events);
        const IN_MEMORY_LIFECYCLE_LIMIT: usize = 4096;
        candidate.retain_recent_lifecycle_events(IN_MEMORY_LIFECYCLE_LIMIT);
        let changed = previous_generation != candidate.generation || !events.is_empty();
        let mut save_outcome = None;
        if changed || commit_provider_promotions {
            let publications = encode_publications(
                &candidate,
                &events,
                self.producer_incarnation,
                &self.identity,
            )?;
            save_outcome = Some(
                self.store
                    .save_refresh(&candidate, &events, &publications)
                    .await?,
            );
        }
        self.metadata = CatalogMetadata::from(self.store.load_runtime_snapshot().await?);
        #[cfg(test)]
        {
            self.catalog = candidate;
        }
        let affected_summary =
            CatalogReconcileSummary::from_save_outcome(save_outcome.as_ref(), &affected);
        log_reconcile_apply_completed(
            &self.metadata,
            changed,
            events.len(),
            &affected_summary,
            started.elapsed().as_millis() as u64,
        );
        self.source_mut()?.mark_promotions_committed();
        Ok(RefreshResult {
            generation: self.metadata.generation,
            event_sequence: self.metadata.event_sequence,
            changed,
            event_count: events.len(),
            events,
        })
    }

    async fn commit_manual_event(
        &mut self,
        catalog: ReferenceCatalog,
        event: LifecycleEvent,
    ) -> ReferenceResult<()> {
        let publications = encode_publications(
            &catalog,
            std::slice::from_ref(&event),
            self.producer_incarnation,
            &self.identity,
        )?;
        self.store
            .save_refresh(&catalog, std::slice::from_ref(&event), &publications)
            .await?;
        self.metadata = CatalogMetadata::from(self.store.load_runtime_snapshot().await?);
        #[cfg(test)]
        {
            self.catalog = catalog;
        }
        Ok(())
    }

    pub async fn upsert_asset(
        &mut self,
        asset: Asset,
        policy: ManualUpsertPolicy,
    ) -> ReferenceResult<()> {
        let mut current = self.store.load().await?.unwrap_or_default();
        let Some(event) = current.upsert_manual_asset(asset, &policy, unix_nanos())? else {
            return Ok(());
        };
        self.commit_manual_event(current, event).await
    }

    pub async fn upsert_instrument(
        &mut self,
        instrument: Instrument,
        policy: ManualUpsertPolicy,
    ) -> ReferenceResult<()> {
        let mut current = self.store.load().await?.unwrap_or_default();
        let Some(event) = current.upsert_manual_instrument(instrument, &policy, unix_nanos())?
        else {
            return Ok(());
        };
        self.commit_manual_event(current, event).await
    }

    pub async fn upsert_listing(
        &mut self,
        listing: Listing,
        policy: ManualUpsertPolicy,
    ) -> ReferenceResult<()> {
        let mut current = self.store.load().await?.unwrap_or_default();
        let Some(event) = current.upsert_manual_listing(listing, &policy, unix_nanos())? else {
            return Ok(());
        };
        self.commit_manual_event(current, event).await
    }

    pub async fn pending_publications(
        &mut self,
        limit: usize,
    ) -> ReferenceResult<Vec<EncodedPublication>> {
        self.publication_outbox.pending_publications(limit).await
    }

    pub async fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        self.publication_outbox.pending_event_count().await
    }

    pub async fn acknowledge_publications(&mut self, event_ids: &[String]) -> ReferenceResult<()> {
        self.publication_outbox
            .acknowledge_publications(event_ids)
            .await
    }
}

#[derive(Debug)]
pub struct RefreshResult {
    pub generation: kairos_primitives::time::Generation,
    pub event_sequence: kairos_primitives::time::Sequence,
    pub changed: bool,
    pub event_count: usize,
    pub events: Vec<LifecycleEvent>,
}
