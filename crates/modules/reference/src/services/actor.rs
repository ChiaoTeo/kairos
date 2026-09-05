//! Single-owner Reference actor.

use kairos_primitives::runtime::{ActorId, InstanceIdentity};
use tracing::info;

use super::providers::ReferenceSourcePlan;
use super::publication::{EncodedPublication, encode_coverage_publications, encode_publications};
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
    pub coverage_count: usize,
    pub usable_coverage_count: usize,
    pub stale_coverage_count: usize,
    pub unavailable_coverage_count: usize,
    pub unresolved_venue_mapping_count: usize,
    pub v2_unprojectable_market_count: usize,
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
            coverage_count: 0,
            usable_coverage_count: 0,
            stale_coverage_count: 0,
            unavailable_coverage_count: 0,
            unresolved_venue_mapping_count: 0,
            v2_unprojectable_market_count: catalog
                .venue_markets
                .values()
                .filter(|market| {
                    catalog
                        .venues
                        .get(&market.execution_venue_id)
                        .is_none_or(|venue| {
                            venue.venue_kind != crate::domain::VenueKind::RegulatedExchange
                        })
                })
                .count(),
        }
    }
}

impl From<super::storage::catalog_store::CatalogRuntimeMetrics> for CatalogMetadata {
    fn from(state: super::storage::catalog_store::CatalogRuntimeMetrics) -> Self {
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
            coverage_count: state.coverage_count,
            usable_coverage_count: state.usable_coverage_count,
            stale_coverage_count: state.stale_coverage_count,
            unavailable_coverage_count: state.unavailable_coverage_count,
            unresolved_venue_mapping_count: state.unresolved_venue_mapping_count,
            v2_unprojectable_market_count: state.v2_unprojectable_market_count,
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
            let state = store.load_runtime_metrics().await?;
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
        if self
            .source_mut()?
            .staged_source_changes()
            .affects_source(self.source_id())
        {
            return self
                .reconcile_normalized(ProviderCatalog::default(), started)
                .await;
        }
        let normalized = self.source_mut()?.normalized_facts_authoritative();
        let incoming = match self
            .source_mut()?
            .advance_workflow_with_budget(connections, budget)
            .await
        {
            Ok(incoming) => incoming,
            Err(error) => {
                self.reconcile_normalized(ProviderCatalog::default(), started)
                    .await?;
                return Err(error);
            },
        };
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
        if self
            .source_mut()?
            .staged_source_changes()
            .affects_source(source_id)
        {
            return self
                .reconcile_normalized(ProviderCatalog::default(), started)
                .await;
        }
        let normalized = self.source_mut()?.normalized_facts_authoritative();
        let outcome = self
            .source_mut()?
            .advance_source_with_budget(source_id, connections, budget)
            .await;
        match outcome {
            Err(error) => {
                self.reconcile_normalized(ProviderCatalog::default(), started)
                    .await?;
                Err(error)
            },
            Ok(Some(incoming)) if normalized => self.reconcile_normalized(incoming, started).await,
            Ok(Some(incoming)) => self.reconcile(incoming, started).await,
            Ok(None) => Ok(RefreshResult {
                generation: self.metadata.generation,
                event_sequence: self.metadata.event_sequence,
                changed: false,
                event_count: 0,
            }),
        }
    }

    async fn reconcile_normalized(
        &mut self,
        overlay: ProviderCatalog,
        started: std::time::Instant,
    ) -> ReferenceResult<RefreshResult> {
        overlay.validate()?;
        let selected_changes = self.source_mut()?.staged_source_changes();
        let selection = self
            .provider_sync_store
            .select_provider_candidate(&overlay, &selected_changes)
            .await?;
        for (scans, error) in &selection.rejected {
            let rejected = crate::services::sources::SourceChanges {
                completed_scans: scans.iter().cloned().collect(),
                ..Default::default()
            };
            self.source_mut()?
                .note_rejected_scans(&rejected, error)
                .await?;
        }
        let result = self
            .reconcile_candidate(selection.catalog, started, Some(selection.accepted))
            .await?;
        if let Some((_, error)) = selection.rejected.into_iter().next() {
            return Err(error);
        }
        Ok(result)
    }

    pub async fn set_source_desired_state(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
    ) -> ReferenceResult<()> {
        self.source_mut()?
            .set_source_desired_state(source_id, desired_state)
            .await?;
        self.reconcile_normalized(ProviderCatalog::default(), std::time::Instant::now())
            .await?;
        Ok(())
    }

    pub async fn set_source_desired_state_with_connections(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.source_mut()?
            .set_source_desired_state_with_connections(source_id, desired_state, connections)
            .await?;
        self.reconcile_normalized(ProviderCatalog::default(), std::time::Instant::now())
            .await?;
        Ok(())
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
        self.reconcile_candidate(incoming, started, None).await
    }

    async fn reconcile_candidate(
        &mut self,
        incoming: ProviderCatalog,
        started: std::time::Instant,
        source_changes: Option<crate::services::sources::SourceChanges>,
    ) -> ReferenceResult<RefreshResult> {
        incoming.validate()?;
        let mut candidate = self.store.load().await?.unwrap_or_default();
        let previous_generation = candidate.generation;
        let mut events = candidate.apply_events(incoming, unix_nanos());
        let catalog_event_count = events.len();
        let tentative_generation = if candidate.generation == previous_generation {
            kairos_primitives::time::Generation::new(previous_generation.get() + 1)
        } else {
            candidate.generation
        };
        let coverage_transitions = if let Some(source_changes) = &source_changes {
            self.provider_sync_store
                .pending_coverage_state_changes(
                    tentative_generation,
                    kairos_primitives::time::Sequence::new(candidate.event_sequence.get() + 1),
                    source_changes,
                )
                .await?
        } else {
            Vec::new()
        };
        if !coverage_transitions.is_empty() {
            candidate.generation = tentative_generation;
            for (_, coverage) in &coverage_transitions {
                let sequence = coverage
                    .event_sequence
                    .expect("coverage transition sequence is allocated before commit");
                events.push(LifecycleEvent {
                    event_id: format!("reference:{sequence:020}"),
                    event_type: "coverage_state_changed".into(),
                    event_time_unix_nanos: coverage.last_attempt_unix_nanos.unwrap_or_default(),
                    record_kind: Some("coverage".into()),
                    record_id: Some(coverage.coverage_id.to_string()),
                    generation: tentative_generation,
                    ..LifecycleEvent::default()
                });
            }
            candidate.event_sequence += coverage_transitions.len() as u64;
            candidate
                .lifecycle_events
                .extend(events.iter().skip(catalog_event_count));
        }
        candidate.retain_recent_lifecycle_events(ReferenceCatalog::LIFECYCLE_HISTORY_LIMIT);
        let changed = previous_generation != candidate.generation || !events.is_empty();
        let mut save_outcome = None;
        if changed || source_changes.is_some() {
            let coverage_publications = encode_coverage_publications(
                &coverage_transitions,
                self.producer_incarnation,
                &self.identity,
            )?;
            let publications = events
                .iter()
                .take(catalog_event_count)
                .map(|event| {
                    crate::services::publication::encode_publication(
                        &candidate,
                        &event,
                        self.producer_incarnation,
                        &self.identity,
                    )
                })
                .chain(coverage_publications.into_iter().map(Ok));
            save_outcome = Some(
                self.store
                    .save_refresh(
                        &candidate,
                        events.iter(),
                        publications,
                        source_changes.as_ref(),
                    )
                    .await?,
            );
        }
        self.metadata = CatalogMetadata::from(self.store.load_runtime_metrics().await?);
        #[cfg(test)]
        {
            self.catalog = candidate;
        }
        // A successful save already returns the exact affected counts. Do not
        // retain a second million-ID set beside persistence's own write set.
        let affected = if save_outcome.is_none() {
            AffectedReferenceSet::from_events(events.iter())
        } else {
            AffectedReferenceSet::default()
        };
        let affected_summary =
            CatalogReconcileSummary::from_save_outcome(save_outcome.as_ref(), &affected);
        log_reconcile_apply_completed(
            &self.metadata,
            changed,
            events.len(),
            &affected_summary,
            started.elapsed().as_millis() as u64,
        );
        if let Some(committed) = &source_changes {
            self.source_mut()?.mark_sources_committed(committed);
        }
        Ok(RefreshResult {
            generation: self.metadata.generation,
            event_sequence: self.metadata.event_sequence,
            changed,
            event_count: events.len(),
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
            .save_refresh(
                &catalog,
                std::iter::once(event.clone()),
                publications.into_iter().map(Ok),
                None,
            )
            .await?;
        self.metadata = CatalogMetadata::from(self.store.load_runtime_metrics().await?);
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
}
