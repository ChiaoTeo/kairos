//! Provider fan-in, last-known-good retention, health, and runtime control.

use super::*;
#[cfg(test)]
use crate::logging::events as log_events;
use crate::services::providers::activation::is_scoped_massive_options_definition;
use crate::services::runtime::{
    SourceRuntimeRegistry, SourceScheduleDecision, SourceScheduleSkipReason,
    source_activation_unavailable_error, source_runtime_error,
};
#[cfg(not(test))]
use crate::services::sources::ConfiguredProviderSource;
use crate::services::sources::{
    log_source_candidate_completed, log_source_retry_scheduled, log_source_scan_completed,
    log_source_scan_failed, log_source_scan_progress, log_source_work_item_skipped,
    log_source_work_skipped, log_source_work_started, source_runtime_progress, source_work_item,
};
#[cfg(test)]
use crate::services::sources::{
    log_source_fan_in_completed, log_source_scan_degraded, log_source_work_completed,
};

/// Reference-owned fan-in for the global catalog. Each provider remains an
/// independent integration connection; this source only merges normalized
/// records before they enter the Reference actor.
pub struct ProviderFanInSource<S> {
    workers: Vec<ProviderWorker<S>>,
    next_worker_index: usize,
    #[cfg(test)]
    last_good: BTreeMap<String, ProviderCatalog>,
    runtime: SourceRuntimeRegistry,
    sync_store: Option<SqlxProviderSyncStore>,
    credential_resolver: ReferenceCredentialResolver,
    staged_changes: crate::services::sources::SourceChanges,
}

#[cfg(not(test))]
impl ProviderFanInSource<ConfiguredProviderSource> {
    pub(crate) fn massive_option_connection_plan(
        &self,
        underlying: &str,
    ) -> ReferenceResult<(
        kairos_conflux::ConnectionKey,
        kairos_conflux::MassiveRestConfig,
    )> {
        self.workers
            .iter()
            .find(|worker| worker.source_id == "massive-options")
            .ok_or_else(|| {
                ReferenceError::Invalid("Massive options source is not configured".into())
            })?
            .source
            .massive_option_connection_plan(underlying)
    }

    pub(crate) async fn set_managed_source_scope(
        &mut self,
        source_id: &str,
        scope: SourceScope,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
    ) -> ReferenceResult<()> {
        let worker = self
            .workers
            .iter_mut()
            .find(|worker| worker.source_id == source_id)
            .ok_or_else(|| {
                ReferenceError::Invalid(format!("{source_id} source is not configured"))
            })?;
        worker
            .source
            .set_managed_source_scope(source_id, scope, enabled, connection_key)
            .await
    }
}

struct ProviderWorker<S> {
    source_id: String,
    source: S,
}

// Each provider refresh advances a bounded page batch. Large providers persist
// their cursor and candidate catalog between refreshes, while only completed
// candidates are promoted to last-known-good.
pub(super) const PROVIDER_FETCH_TIMEOUT: Duration = Duration::from_secs(150);
pub(super) const MASSIVE_PAGE_TIMEOUT: Duration = Duration::from_secs(20);

impl<S> ProviderFanInSource<S>
where
    S: ReferenceSource,
{
    #[cfg(test)]
    pub async fn new_with_sync_store(
        sources: Vec<S>,
        sync_store: Option<SqlxProviderSyncStore>,
    ) -> ReferenceResult<Self> {
        Self::new_with_sync_store_and_credentials(
            sources,
            sync_store,
            ReferenceCredentialResolver::default(),
        )
        .await
    }

    pub(crate) async fn new_with_sync_store_and_credentials(
        sources: Vec<S>,
        mut sync_store: Option<SqlxProviderSyncStore>,
        credential_resolver: ReferenceCredentialResolver,
    ) -> ReferenceResult<Self> {
        if sources.is_empty() {
            return Err(ReferenceError::Provider(
                "reference catalog has no sources".into(),
            ));
        }
        #[cfg(not(test))]
        if !sync_store
            .as_ref()
            .is_some_and(SqlxProviderSyncStore::supports_normalized_promotion)
        {
            return Err(ReferenceError::Persistence(
                "production reference fan-in requires normalized SQLite promotion".into(),
            ));
        }
        #[cfg(test)]
        let mut last_good = BTreeMap::new();
        let mut runtime = SourceRuntimeRegistry::default();
        let persisted_definitions = if let Some(store) = sync_store.as_mut() {
            store.source_definitions().await?
        } else {
            Vec::new()
        };
        let persisted_by_source = persisted_definitions
            .iter()
            .cloned()
            .map(|definition| (definition.source_id.to_string(), definition))
            .collect::<BTreeMap<_, _>>();
        for source in &sources {
            let seed = source.source_definition()?;
            let definition = persisted_by_source
                .get(seed.source_id.as_str())
                .cloned()
                .unwrap_or_else(|| seed.clone());
            runtime.register_definition(definition.clone());
            if let Some(store) = sync_store.as_mut() {
                if store.supports_normalized_promotion() {
                    // Built-in public sources are first-start seeds. Once a
                    // definition exists, the Reference-owned registry is the
                    // authority and startup must not overwrite its scope,
                    // connection binding, policy, or desired state.
                    if !persisted_by_source.contains_key(seed.source_id.as_str()) {
                        store.upsert_source_definition(definition).await?;
                    }
                    if store.has_last_good(source.source_id()).await? {
                        runtime.note_last_good(source.source_id().to_owned());
                    }
                    continue;
                }
                #[cfg(test)]
                if let Some(catalog) = store.load_last_good(source.source_id()).await? {
                    if provider_catalog_uses_current_canonical_shape(&catalog) {
                        last_good.insert(source.source_id().to_owned(), catalog);
                        runtime.note_last_good(source.source_id().to_owned());
                    } else {
                        let log_event = log_events::SOURCE_WORK_DEGRADED;
                        tracing::warn!(
                            event = log_event.event,
                            component = log_event.component,
                            area = log_event.area,
                            action = log_event.action,
                            outcome = log_event.outcome,
                            legacy_event = "reference_provider_snapshot_schema_mismatch",
                            source_id = source.source_id(),
                            "persisted provider snapshot uses an obsolete canonical shape and will be refreshed before reuse"
                        );
                    }
                }
            }
        }
        if sync_store.is_some() {
            for definition in persisted_definitions {
                runtime.register_definition(definition);
            }
        }
        let desired_states = runtime
            .definitions()
            .map(|definition| (definition.source_id.to_string(), definition.desired_state))
            .collect::<Vec<_>>();
        runtime.set_desired_states(desired_states);
        let workers = sources
            .into_iter()
            .map(|source| {
                let source_id = source.source_id().to_owned();
                ProviderWorker { source_id, source }
            })
            .collect();
        Ok(Self {
            workers,
            next_worker_index: 0,
            #[cfg(test)]
            last_good,
            runtime,
            sync_store,
            credential_resolver,
            staged_changes: crate::services::sources::SourceChanges::default(),
        })
    }

    /// Build a catalog from already committed provider snapshots. This is used
    /// after one independently scheduled source finishes so the completion
    /// never triggers network work for Binance, OKX, or another peer.
    #[cfg(test)]
    fn last_good_catalog(&self) -> ReferenceResult<Option<ProviderCatalog>> {
        if self.last_good.is_empty() {
            return Ok(None);
        }
        ProviderCatalog::merge(self.last_good.values()).map(Some)
    }

    async fn fetch_normalized_facts(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<ProviderCatalog> {
        let tick_started = Instant::now();
        let wall_clock_budget = budget
            .max_wall_clock_millis
            .map(std::time::Duration::from_millis);
        let mut requests = Vec::new();
        let mut inactive = Vec::new();
        let mut retry_deferred = Vec::new();
        let mut budget_deferred = Vec::new();
        let mut admitted_sources = 0u32;
        let worker_count = self.workers.len();
        let mut last_admitted_index = None;
        let start_index = self.next_worker_index.min(worker_count);
        let (head, tail) = self.workers.split_at_mut(start_index);
        for (index, worker) in tail
            .iter_mut()
            .enumerate()
            .map(|(offset, worker)| (start_index + offset, worker))
            .chain(head.iter_mut().enumerate())
        {
            if self.staged_changes.affects_source(&worker.source_id) {
                log_source_work_skipped(&worker.source_id, SourceScheduleSkipReason::CommitPending);
                continue;
            }
            let work_item =
                match self
                    .runtime
                    .schedule_source_tick(&worker.source_id, Instant::now(), budget)
                {
                    SourceScheduleDecision::Scheduled(work_item) => work_item,
                    SourceScheduleDecision::Skipped(
                        reason @ SourceScheduleSkipReason::Inactive,
                    ) => {
                        log_source_work_skipped(&worker.source_id, reason);
                        inactive.push(worker.source_id.clone());
                        continue;
                    },
                    SourceScheduleDecision::Skipped(
                        reason @ SourceScheduleSkipReason::RetryWaiting,
                    ) => {
                        log_source_work_skipped(&worker.source_id, reason);
                        retry_deferred.push(worker.source_id.clone());
                        continue;
                    },
                    SourceScheduleDecision::Skipped(reason) => {
                        log_source_work_skipped(&worker.source_id, reason);
                        budget_deferred.push(worker.source_id.clone());
                        continue;
                    },
                };
            let wall_clock_exhausted = admitted_sources > 0
                && wall_clock_budget.is_some_and(|budget| tick_started.elapsed() >= budget);
            if admitted_sources >= budget.max_sources_per_tick || wall_clock_exhausted {
                let skip_reason = if wall_clock_exhausted {
                    SourceScheduleSkipReason::TickWallClockBudget
                } else {
                    SourceScheduleSkipReason::TickSourceBudget
                };
                self.runtime.mark_deferred(&work_item, skip_reason);
                log_source_work_item_skipped(&work_item, skip_reason);
                budget_deferred.push(work_item.source_id.to_string());
                continue;
            }
            admitted_sources = admitted_sources.saturating_add(1);
            last_admitted_index = Some(index);
            self.runtime.mark_scheduled(&work_item);
            log_source_retry_scheduled(&work_item);
            log_source_work_started(&work_item);
            let source_id = work_item.source_id.clone();
            requests.push(async move {
                let mut result = tokio::time::timeout(
                    PROVIDER_FETCH_TIMEOUT,
                    worker
                        .source
                        .advance_workflow_step_with_budget(connections, budget),
                )
                .await
                .map_err(|error| {
                    ReferenceError::Provider(format!("provider fetch timed out: {error}"))
                })
                .and_then(|result| result);
                if let Ok(update) = &mut result {
                    update.note_scheduled_work_item(&work_item);
                }
                (source_id, result)
            });
        }
        if let Some(index) = last_admitted_index {
            self.next_worker_index = (index + 1) % worker_count.max(1);
        }
        let requests = futures_util::future::join_all(requests).await;
        if self.sync_store.is_none() {
            return Err(ReferenceError::Persistence(
                "normalized provider store is unavailable".into(),
            ));
        }
        let mut unavailable = Vec::new();
        let mut unavailable_due_to_failure = false;
        for source_id in inactive {
            self.runtime.mark_current_inactive(&source_id);
        }
        for source_id in retry_deferred {
            if !self
                .sync_store
                .as_mut()
                .expect("store checked")
                .has_last_good(&source_id)
                .await?
            {
                unavailable.push(source_id);
                unavailable_due_to_failure = true;
            }
        }
        for source_id in budget_deferred {
            if !self
                .sync_store
                .as_mut()
                .expect("store checked")
                .has_last_good(&source_id)
                .await?
            {
                unavailable.push(source_id);
            }
        }
        for (source_id, result) in requests {
            match result {
                Ok(update) if update.complete => {
                    if update.staged_changes.is_none() {
                        update.catalog.validate()?;
                        self.sync_store
                            .as_mut()
                            .expect("store checked")
                            .save_last_good(&source_id, &update.catalog)
                            .await?;
                    }
                    self.record_staged_changes(&source_id, &update)?;
                    self.mark_promoting(&source_id, &update);
                    log_source_scan_completed(&source_id, &update, Some(true));
                    log_source_candidate_completed(&source_id, &update);
                },
                Ok(update) => {
                    self.mark_syncing(&source_id, &update);
                    let has_last_good = self
                        .sync_store
                        .as_mut()
                        .expect("store checked")
                        .has_last_good(&source_id)
                        .await?;
                    log_source_scan_progress(&source_id, &update, Some(has_last_good));
                    if !has_last_good {
                        unavailable.push(source_id.to_string());
                    }
                },
                Err(error) => {
                    let has_last_good = self
                        .sync_store
                        .as_mut()
                        .expect("store checked")
                        .has_last_good(&source_id)
                        .await?;
                    self.sync_store
                        .as_mut()
                        .expect("store checked")
                        .set_source_failure(&source_id, has_last_good)
                        .await?;
                    self.mark_failure(&source_id, has_last_good, &error);
                    log_source_scan_failed(&source_id, &error, has_last_good);
                    if !has_last_good {
                        unavailable.push(source_id.to_string());
                        unavailable_due_to_failure = true;
                    }
                },
            }
        }
        if !unavailable.is_empty() {
            if !unavailable_due_to_failure {
                return Err(ReferenceError::SyncInProgress {
                    providers: unavailable,
                });
            }
            return Err(ReferenceError::Provider(format!(
                "providers unavailable without last-known-good facts: {}",
                unavailable.join(", ")
            )));
        }
        Ok(ProviderCatalog::default())
    }
}

#[cfg(test)]
impl<S: ReferenceSource> ProviderFanInSource<S> {
    pub async fn new(sources: Vec<S>) -> ReferenceResult<Self> {
        Self::new_with_sync_store(sources, None).await
    }
}

#[async_trait::async_trait(?Send)]
impl<S> ReferenceSource for ProviderFanInSource<S>
where
    S: ReferenceSource,
{
    fn normalized_facts_authoritative(&self) -> bool {
        self.sync_store
            .as_ref()
            .is_some_and(SqlxProviderSyncStore::supports_normalized_promotion)
    }

    fn source_id(&self) -> &str {
        if self.workers.len() == 1 {
            &self.workers[0].source_id
        } else {
            "reference-default"
        }
    }

    async fn advance_workflow(&mut self) -> ReferenceResult<ProviderCatalog> {
        #[cfg(not(test))]
        return Err(ReferenceError::Invalid(
            "reference fan-in requires Conflux-managed connections".into(),
        ));
        #[cfg(test)]
        {
            let mut system = kairos_conflux::ConfluxSystem::new();
            self.advance_workflow_with_connections(&mut system.connections())
                .await
        }
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow().await
    }

    async fn advance_workflow_with_connections(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow_with_budget(connections, SourceTickBudget::default())
            .await
    }

    async fn advance_workflow_with_budget(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<ProviderCatalog> {
        if self.normalized_facts_authoritative() {
            return self.fetch_normalized_facts(connections, budget).await;
        }
        #[cfg(not(test))]
        return Err(ReferenceError::Persistence(
            "production reference sources require normalized SQLite promotion".into(),
        ));
        #[cfg(test)]
        {
            // Provider queries are polled concurrently by the caller's runtime.
            // Each query has the same bounded deadline, so one slow provider does
            // not serialize or indefinitely hold the fan-in.
            let started = Instant::now();
            let mut failures = Vec::new();
            let mut unavailable_without_last_good = Vec::new();
            let mut requests = Vec::new();
            let mut inactive_sources = Vec::new();
            for worker in &mut self.workers {
                if self.runtime.is_inactive(&worker.source_id) {
                    inactive_sources.push(worker.source_id.clone());
                    continue;
                }
                if self
                    .runtime
                    .retry_waiting(&worker.source_id, Instant::now())
                {
                    failures.push(format!(
                        "{}: provider retry window is waiting",
                        worker.source_id
                    ));
                    if !self.last_good.contains_key(&worker.source_id) {
                        unavailable_without_last_good.push(worker.source_id.clone());
                    }
                    continue;
                }
                self.runtime.mark_attempt(&worker.source_id);
                let source_id = worker.source_id.clone();
                let source = &mut worker.source;
                requests.push(async move {
                    let provider_started = Instant::now();
                    let result = match tokio::time::timeout(
                        PROVIDER_FETCH_TIMEOUT,
                        source.advance_workflow_step(),
                    )
                    .await
                    {
                        Ok(result) => result,
                        Err(error) => Err(ReferenceError::Provider(format!(
                            "provider fetch timed out: {error}"
                        ))),
                    };
                    (source_id, result, provider_started)
                });
            }
            let requests = join_all(requests).await;
            for source_id in inactive_sources {
                self.mark_current_inactive(&source_id);
            }
            let mut successful_sources = 0usize;
            for (source_id, result, provider_started) in requests {
                log_source_work_completed(
                    &source_id,
                    provider_started.elapsed().as_millis() as u64,
                    result.is_ok(),
                    result
                        .as_ref()
                        .map(|update| update.complete)
                        .unwrap_or(false),
                    result.as_ref().map(|update| update.page_count).unwrap_or(0),
                    "reference_provider_fetch_completed",
                );
                match result {
                    Ok(update) => {
                        if !update.complete {
                            self.mark_syncing(&source_id, &update);
                            log_source_scan_progress(
                                &source_id,
                                &update,
                                Some(self.last_good.contains_key(&source_id)),
                            );
                            if !self.last_good.contains_key(&source_id) {
                                unavailable_without_last_good.push(source_id.clone());
                                continue;
                            }
                        } else {
                            successful_sources += 1;
                            self.mark_ready(&source_id, &update);
                            log_source_scan_completed(&source_id, &update, Some(true));
                            let catalog = update.catalog;
                            self.last_good.insert(source_id.clone(), catalog);
                            if let Some(store) = self.sync_store.as_mut() {
                                store
                                    .save_last_good(
                                        &source_id,
                                        self.last_good
                                            .get(&source_id)
                                            .expect("inserted provider snapshot"),
                                    )
                                    .await?;
                            }
                        }
                    },
                    Err(error) => {
                        failures.push(format!("{source_id}: {error}"));
                        self.mark_failure(
                            &source_id,
                            self.last_good.contains_key(&source_id),
                            &error,
                        );
                        if !self.last_good.contains_key(&source_id) {
                            unavailable_without_last_good.push(source_id.clone());
                            continue;
                        }
                    },
                };
            }
            if !unavailable_without_last_good.is_empty() {
                #[cfg(not(test))]
                self.last_good.clear();
                if failures.is_empty() {
                    return Err(ReferenceError::SyncInProgress {
                        providers: unavailable_without_last_good,
                    });
                }
                return Err(ReferenceError::Provider(format!(
                    "reference providers unavailable without a last-known-good snapshot: {}; failures: {}",
                    unavailable_without_last_good.join(", "),
                    failures.join("; ")
                )));
            }
            if successful_sources == 0 && self.last_good.is_empty() {
                #[cfg(not(test))]
                self.last_good.clear();
                return Err(ReferenceError::Provider(format!(
                    "all reference providers failed: {}",
                    failures.join("; ")
                )));
            }
            if !failures.is_empty() {
                log_source_scan_degraded(&failures);
            }
            log_source_fan_in_completed(
                self.workers.len(),
                successful_sources,
                started.elapsed().as_millis() as u64,
            );
            // Include inactive and circuit-open providers through their durable
            // snapshots without cloning them into a synthetic request result.
            // This also makes the returned fan-in independent from which workers
            // happened to be scheduled in this refresh cycle.
            let result = ProviderCatalog::merge(self.last_good.values());
            #[cfg(not(test))]
            self.last_good.clear();
            result
        }
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow_with_connections(connections).await
    }

    #[cfg(test)]
    async fn advance_one_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        self.advance_source_with_connections(source_id, &mut system.connections())
            .await
    }

    async fn advance_source_with_connections(
        &mut self,
        source_id: &str,
        connections: &kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        self.advance_source_with_budget(source_id, connections, SourceTickBudget::default())
            .await
    }

    async fn advance_source_with_budget(
        &mut self,
        source_id: &str,
        connections: &kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        let Some(index) = self
            .workers
            .iter()
            .position(|worker| worker.source_id == source_id)
        else {
            if self.runtime.is_inactive(source_id) {
                self.mark_current_inactive(source_id);
                return Ok(None);
            }
            return Err(self
                .runtime
                .registered_source_without_adapter_error(source_id));
        };
        if self.normalized_facts_authoritative() {
            if self.staged_changes.affects_source(source_id) {
                log_source_work_skipped(source_id, SourceScheduleSkipReason::CommitPending);
                return Ok(Some(ProviderCatalog::default()));
            }
            let Some(work_item) =
                self.runtime
                    .source_refresh_work_item(source_id, Instant::now(), budget)?
            else {
                self.mark_current_inactive(source_id);
                return Ok(None);
            };
            self.runtime.mark_scheduled(&work_item);
            let result = tokio::time::timeout(
                PROVIDER_FETCH_TIMEOUT,
                self.workers[index]
                    .source
                    .advance_workflow_step_with_budget(connections, budget),
            )
            .await
            .map_err(|error| ReferenceError::Provider(format!("provider fetch timed out: {error}")))
            .and_then(|result| result);
            let mut result = result;
            if let Ok(update) = &mut result {
                update.note_scheduled_work_item(&work_item);
            }
            return match result {
                Ok(update) if update.complete => {
                    if update.staged_changes.is_none() {
                        update.catalog.validate()?;
                        self.sync_store
                            .as_mut()
                            .expect("normalized source has a store")
                            .save_last_good(source_id, &update.catalog)
                            .await?;
                    }
                    self.record_staged_changes(source_id, &update)?;
                    self.mark_promoting(source_id, &update);
                    log_source_candidate_completed(source_id, &update);
                    Ok(Some(ProviderCatalog::default()))
                },
                Ok(update) => {
                    self.mark_syncing(source_id, &update);
                    Ok(None)
                },
                Err(error) => {
                    let has_last_good = self
                        .sync_store
                        .as_mut()
                        .expect("normalized source has a store")
                        .has_last_good(source_id)
                        .await?;
                    self.sync_store
                        .as_mut()
                        .expect("normalized source has a store")
                        .set_source_failure(source_id, has_last_good)
                        .await?;
                    self.mark_failure(source_id, has_last_good, &error);
                    Err(error)
                },
            };
        }
        #[cfg(not(test))]
        return Err(ReferenceError::Persistence(
            "production reference sources require normalized SQLite promotion".into(),
        ));
        #[cfg(test)]
        {
            self.runtime.mark_attempt(source_id);
            let started = Instant::now();
            let result = {
                let source = &mut self.workers[index].source;
                match tokio::time::timeout(
                    PROVIDER_FETCH_TIMEOUT,
                    source.advance_workflow_step_with_budget(connections, budget),
                )
                .await
                {
                    Ok(result) => result,
                    Err(error) => Err(ReferenceError::Provider(format!(
                        "provider fetch timed out: {error}"
                    ))),
                }
            };
            log_source_work_completed(
                source_id,
                started.elapsed().as_millis() as u64,
                result.is_ok(),
                result
                    .as_ref()
                    .map(|update| update.complete)
                    .unwrap_or(false),
                result.as_ref().map(|update| update.page_count).unwrap_or(0),
                "reference_provider_targeted_fetch_completed",
            );
            match result {
                Ok(update) if !update.complete => {
                    self.mark_syncing(source_id, &update);
                    #[cfg(not(test))]
                    self.last_good.clear();
                    Ok(None)
                },
                Ok(update) => {
                    self.mark_promoting(source_id, &update);
                    self.last_good.insert(source_id.to_owned(), update.catalog);
                    if let Some(store) = self.sync_store.as_mut() {
                        store
                            .save_last_good(
                                source_id,
                                self.last_good
                                    .get(source_id)
                                    .expect("inserted provider snapshot"),
                            )
                            .await?;
                    }
                    let result = self.last_good_catalog();
                    #[cfg(not(test))]
                    self.last_good.clear();
                    result
                },
                Err(error) => {
                    self.mark_failure(source_id, self.last_good.contains_key(source_id), &error);
                    #[cfg(not(test))]
                    self.last_good.clear();
                    Err(error)
                },
            }
        }
    }

    async fn set_source_desired_state(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
    ) -> ReferenceResult<()> {
        if !self
            .workers
            .iter()
            .any(|worker| worker.source_id == source_id)
            && !self.runtime.source_ids().any(|known| known == source_id)
        {
            return Err(ReferenceError::Invalid(format!(
                "unknown reference source: {source_id}"
            )));
        }
        let store = self.sync_store.as_mut().ok_or_else(|| {
            ReferenceError::Persistence("reference source control store is unavailable".into())
        })?;
        let removed_scans = if desired_state == SourceDesiredState::Removed {
            store.source_scan_ids(source_id).await?
        } else {
            Vec::new()
        };
        store
            .set_source_desired_state(source_id, desired_state)
            .await?;
        for scan in removed_scans {
            self.staged_changes.completed_scans.remove(&scan);
            self.staged_changes.removed_scans.insert(scan);
        }
        self.runtime.apply_desired_state(source_id, desired_state);
        Ok(())
    }

    async fn set_source_desired_state_with_connections(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.set_source_desired_state(source_id, desired_state)
            .await?;
        if desired_state != SourceDesiredState::Removed {
            return Ok(());
        }
        let Some(definition) = self
            .source_health()
            .into_iter()
            .find(|health| health.source_id == source_id)
            .and_then(|health| health.definition)
        else {
            return Ok(());
        };
        if source_id == "massive-options" {
            let Some(worker) = self
                .workers
                .iter_mut()
                .find(|worker| worker.source_id == source_id)
            else {
                return Ok(());
            };
            let underlyings = worker.source.option_underlyings();
            for underlying in underlyings {
                worker
                    .source
                    .set_source_scope_with_connections(
                        source_id,
                        SourceScope::underlying_instrument(underlying),
                        false,
                        connections,
                    )
                    .await?;
            }
            self.workers.retain(|worker| worker.source_id != source_id);
            return Ok(());
        }
        if S::deactivate_source_definition(&definition, connections)? {
            self.workers.retain(|worker| worker.source_id != source_id);
        }
        Ok(())
    }

    async fn upsert_source_definition(
        &mut self,
        definition: ReferenceSourceDefinition,
    ) -> ReferenceResult<()> {
        let store = self.sync_store.as_mut().ok_or_else(|| {
            ReferenceError::Persistence("reference source registry store is unavailable".into())
        })?;
        store.upsert_source_definition(definition.clone()).await?;
        self.runtime.register_definition(definition.clone());
        self.runtime
            .set_desired_states([(definition.source_id.to_string(), definition.desired_state)]);
        Ok(())
    }

    async fn upsert_source_definition_with_connections(
        &mut self,
        definition: ReferenceSourceDefinition,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.upsert_source_definition(definition.clone()).await?;
        if self
            .workers
            .iter()
            .any(|worker| worker.source_id == definition.source_id.as_str())
        {
            if is_scoped_massive_options_definition(&definition) {
                self.set_source_scope_with_connections(
                    definition.source_id.as_str(),
                    definition.scope.clone(),
                    definition.desired_state == SourceDesiredState::Enabled,
                    connections,
                )
                .await?;
            }
            return Ok(());
        }
        let source = match S::activate_source_definition(
            &definition,
            connections,
            &self.credential_resolver,
            self.sync_store.clone(),
        )
        .await
        {
            Ok(Some(source)) => source,
            Ok(None) => {
                if definition.desired_state == SourceDesiredState::Enabled {
                    self.runtime.mark_registration_error(
                        definition.source_id.as_str(),
                        source_activation_unavailable_error(&definition),
                    );
                }
                return Ok(());
            },
            Err(error) => {
                self.runtime.mark_registration_error(
                    definition.source_id.as_str(),
                    source_runtime_error(&error),
                );
                return Err(error);
            },
        };
        let source_id = source.source_id().to_owned();
        self.workers.push(ProviderWorker { source_id, source });
        Ok(())
    }

    async fn set_source_scope_with_connections(
        &mut self,
        source_id: &str,
        scope: SourceScope,
        enabled: bool,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        let Some(worker) = self
            .workers
            .iter_mut()
            .find(|worker| worker.source_id == source_id)
        else {
            return Err(ReferenceError::Invalid(format!(
                "{source_id} source is not configured"
            )));
        };
        worker
            .source
            .set_source_scope_with_connections(source_id, scope, enabled, connections)
            .await
    }

    fn option_underlyings(&self) -> Vec<String> {
        self.workers
            .iter()
            .find(|worker| worker.source_id == "massive-options")
            .map(|worker| worker.source.option_underlyings())
            .unwrap_or_default()
    }

    fn source_health(&self) -> Vec<SourceHealth> {
        self.runtime.source_health_for_active_sources(
            self.workers.iter().map(|worker| worker.source_id.as_str()),
        )
    }

    fn mark_sources_committed(&mut self, committed: &crate::services::sources::SourceChanges) {
        self.staged_changes
            .completed_scans
            .retain(|source| !committed.completed_scans.contains(source));
        self.staged_changes
            .removed_scans
            .retain(|source| !committed.removed_scans.contains(source));
        self.runtime
            .mark_sources_committed(committed, &self.staged_changes);
    }

    fn staged_source_changes(&self) -> crate::services::sources::SourceChanges {
        self.staged_changes.clone()
    }

    async fn note_rejected_scans(
        &mut self,
        rejected: &crate::services::sources::SourceChanges,
        error: &ReferenceError,
    ) -> ReferenceResult<()> {
        let sources = self
            .workers
            .iter()
            .filter(|worker| rejected.affects_source(&worker.source_id))
            .map(|worker| worker.source_id.clone())
            .collect::<Vec<_>>();
        for source_id in sources {
            let store = self.sync_store.as_mut().ok_or_else(|| {
                ReferenceError::Persistence("normalized provider store is unavailable".into())
            })?;
            let has_last_good = store.has_last_good(&source_id).await?;
            store.set_source_failure(&source_id, has_last_good).await?;
            self.mark_failure(&source_id, has_last_good, error);
        }
        Ok(())
    }
}

#[cfg(test)]
pub(super) fn provider_catalog_uses_current_canonical_shape(catalog: &ProviderCatalog) -> bool {
    if catalog
        .exchanges
        .iter()
        .any(|value| value.source_id.is_some())
        || catalog.assets.iter().any(|value| value.source_id.is_some())
        || catalog
            .instruments
            .iter()
            .any(|value| value.source_id.is_some())
    {
        return false;
    }
    catalog.instruments.iter().all(|value| {
        let id = value.instrument_id.as_str();
        if let Some(base) = id.strip_prefix("instrument:spot:") {
            return value.instrument_type == InstrumentKind::Spot
                && value.symbol.as_str() == base
                && value
                    .primary_currency_asset_id
                    .as_ref()
                    .is_some_and(|asset| asset.as_str() == format!("asset:crypto:{base}"));
        }
        let expected = if id.starts_with("instrument:perpetual:") {
            Some(("perpetual", "instrument:perpetual:"))
        } else if id.starts_with("instrument:future:") {
            Some(("future", "instrument:future:"))
        } else if id.starts_with("instrument:option:") {
            Some(("option", "instrument:option:"))
        } else {
            None
        };
        let Some((family, prefix)) = expected else {
            return true;
        };
        let canonical_tail = id.strip_prefix(prefix).expect("prefix checked");
        let symbol = canonical_tail.replace(':', "-");
        let expiry_is_compact = family == "perpetual"
            || canonical_tail.split(':').nth(1).is_some_and(|expiry| {
                expiry.len() == 8 && expiry.chars().all(|ch| ch.is_ascii_digit())
            });
        value.instrument_type.as_str() == family
            && value.symbol.as_str() == symbol
            && expiry_is_compact
    })
}

impl<S> ProviderFanInSource<S>
where
    S: ReferenceSource,
{
    fn record_staged_changes(
        &mut self,
        source_id: &str,
        update: &SourceUpdate,
    ) -> ReferenceResult<()> {
        let changes =
            update
                .staged_changes
                .clone()
                .unwrap_or(crate::services::sources::SourceChanges {
                    completed_scans: std::collections::BTreeSet::from([ReferenceSourceId::new(
                        source_id,
                    )?]),
                    removed_scans: std::collections::BTreeSet::new(),
                });
        for source_id in changes.completed_scans {
            self.staged_changes.removed_scans.remove(&source_id);
            self.staged_changes.completed_scans.insert(source_id);
        }
        for source_id in changes.removed_scans {
            self.staged_changes.completed_scans.remove(&source_id);
            self.staged_changes.removed_scans.insert(source_id);
        }
        Ok(())
    }

    fn mark_promoting(&mut self, source_id: &str, update: &SourceUpdate) {
        self.runtime.mark_promoting(
            source_id,
            source_runtime_progress(update, true),
            source_work_item(update),
        );
    }

    #[cfg(test)]
    fn mark_ready(&mut self, source_id: &str, update: &SourceUpdate) {
        self.runtime.mark_success(
            source_id,
            source_runtime_progress(update, true),
            source_work_item(update),
        );
    }

    fn mark_failure(&mut self, source_id: &str, has_last_good: bool, error: &ReferenceError) {
        self.runtime
            .mark_failure(source_id, has_last_good, Some(source_runtime_error(error)));
    }

    fn mark_syncing(&mut self, source_id: &str, update: &SourceUpdate) {
        self.runtime.mark_syncing(
            source_id,
            source_runtime_progress(update, false),
            source_work_item(update),
        );
    }

    fn mark_current_inactive(&mut self, source_id: &str) {
        self.runtime.mark_current_inactive(source_id);
    }
}
