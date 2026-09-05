//! Process status mapping for the control contract.

use kairos_primitives::reference::InstrumentId;
use kairos_reference_contract::{
    ReferenceAppPhase, ReferenceAppRuntimeError, ReferenceAppRuntimeStatus,
    ReferenceCatalogIntegrityStatus, ReferenceCatalogReadiness, ReferenceCatalogRuntimeStatus,
    ReferenceCoverageRuntimeStatus, ReferenceHealthResponse, ReferenceHealthStatus,
    ReferenceProviderHealth, ReferenceProviderStatus, ReferencePublicationRuntimeError,
    ReferencePublicationRuntimeStatus, ReferenceRuntimeStatus, ReferenceRuntimeStatusResponse,
    ReferenceSourceDesiredState, ReferenceSourceKind, ReferenceSourcePhase,
    ReferenceSourceProgress, ReferenceSourceProgressKind, ReferenceSourceRuntimeError,
    ReferenceSourceRuntimeStatus, ReferenceSourceScope, ReferenceSourceScopeKind,
    ReferenceSourceSyncPolicy, ReferenceSourceTickBudget, ReferenceSourceWorkItem,
};

use super::diagnostics::{publication_backlog_degraded, runtime_diagnostics};
use crate::application::{ReferenceApplication, ReferenceApplicationPhase, ReferenceReadModel};
use crate::domain::{
    ReferenceSourceDefinition, SourceDesiredState, SourceRuntimePhase, SourceRuntimeProgressKind,
    SourceScope, SourceScopeKind, SourceSyncPolicy,
};

const DEFAULT_PUBLICATION_BATCH_LIMIT: usize = 1_024;

impl ReferenceApplication {
    pub(crate) async fn contract_health(&mut self) -> ReferenceHealthResponse {
        let model = self.read_model().await;
        let providers = model
            .source_health()
            .iter()
            .map(|provider| ReferenceProviderHealth {
                source_id: provider.source_id.clone(),
                status: contract_provider_health_status(provider.status),
                stale: provider.stale,
            })
            .collect::<Vec<_>>();
        let degraded = providers.iter().any(|provider| {
            provider.stale || !matches!(provider.status, ReferenceProviderStatus::Ready)
        });
        ReferenceHealthResponse {
            status: if degraded {
                ReferenceHealthStatus::Degraded
            } else {
                ReferenceHealthStatus::Ready
            },
            providers,
        }
    }

    pub(crate) async fn contract_runtime_status(&mut self) -> ReferenceRuntimeStatusResponse {
        let model = self.read_model().await;
        let sources = model
            .source_health()
            .iter()
            .map(source_runtime_status)
            .collect::<Vec<_>>();
        let outbox_depth = model.outbox_depth();
        let catalog_integrity = catalog_integrity_status(&model);
        let runtime_status = runtime_status(
            &sources,
            model.market_count(),
            outbox_depth,
            catalog_integrity.degraded,
        );
        let catalog_readiness = catalog_readiness(
            runtime_status,
            model.market_count(),
            catalog_integrity.degraded,
        );
        let publication_error = self.last_publication_error().cloned();
        let diagnostics = runtime_diagnostics(
            &sources,
            outbox_depth,
            publication_error.as_ref(),
            &catalog_integrity,
        );
        let work_summary = app_runtime_work_summary(&sources);
        let tick_timing = self.tick_timing();
        ReferenceRuntimeStatusResponse {
            status: runtime_status,
            app_runtime: ReferenceAppRuntimeStatus {
                phase: contract_app_phase(self.app_phase()),
                actor_id: model.actor_id().clone(),
                source_id: model.source_id().clone(),
                refresh_interval_millis: self.refresh_interval().as_millis() as u64,
                last_tick_started_unix_nanos: tick_timing.last_started_unix_nanos,
                last_tick_finished_unix_nanos: tick_timing.last_finished_unix_nanos,
                last_tick_duration_millis: tick_timing.last_duration_millis,
                next_tick_due_unix_nanos: tick_timing.next_due_unix_nanos,
                active_work_item_count: work_summary.active_work_item_count,
                queued_work_item_count: work_summary.queued_work_item_count,
                last_error: self
                    .last_tick_error()
                    .map(|error| ReferenceAppRuntimeError {
                        code: error.code.clone(),
                        retryable: error.retryable,
                        message: error.message.clone(),
                    }),
                tick_budget: contract_tick_budget(self.tick_budget()),
            },
            catalog: ReferenceCatalogRuntimeStatus {
                readiness: catalog_readiness,
                generation: model.generation(),
                event_sequence: model.event_sequence(),
                committed_at_unix_nanos: model.committed_at_unix_nanos(),
                exchange_count: model.exchange_count() as u64,
                asset_count: model.asset_count() as u64,
                instrument_count: model.instrument_count() as u64,
                listing_count: model.listing_count() as u64,
                market_count: model.market_count() as u64,
                active_market_count: model.active_market_count() as u64,
                lifecycle_event_count: model.lifecycle_event_count() as u64,
                integrity: catalog_integrity,
            },
            sources,
            coverage: {
                let canonical_conflict_counts = model
                    .source_health()
                    .iter()
                    .filter_map(|source| source.last_error.as_ref())
                    .filter(|error| error.code == "reference.canonical_conflict")
                    .fold(std::collections::BTreeMap::new(), |mut counts, error| {
                        *counts
                            .entry(
                                error
                                    .record_kind
                                    .clone()
                                    .unwrap_or_else(|| "unknown".into()),
                            )
                            .or_insert(0) += 1;
                        counts
                    });
                ReferenceCoverageRuntimeStatus {
                    option_underlyings: self
                        .option_underlyings()
                        .into_iter()
                        .filter_map(|value| InstrumentId::new(value).ok())
                        .collect(),
                    coverage_count: model.coverage_count() as u64,
                    usable_coverage_count: model.usable_coverage_count() as u64,
                    stale_coverage_count: model.stale_coverage_count() as u64,
                    unavailable_coverage_count: model.unavailable_coverage_count() as u64,
                    unresolved_venue_mapping_count: model.unresolved_venue_mapping_count() as u64,
                    v2_unprojectable_market_count: model.v2_unprojectable_market_count() as u64,
                    canonical_conflict_count: canonical_conflict_counts.values().sum(),
                    canonical_conflict_counts,
                }
            },
            publication: ReferencePublicationRuntimeStatus {
                pending_publication_count: outbox_depth as u64,
                backlog_degraded: publication_backlog_degraded(outbox_depth),
                batch_limit: publication_batch_limit(self.tick_budget()) as u64,
                oldest_pending_event_id: model
                    .oldest_pending_publication_event_id()
                    .map(str::to_owned),
                last_error: self.last_publication_error().map(|error| {
                    ReferencePublicationRuntimeError {
                        code: error.code.clone(),
                        retryable: error.retryable,
                        message: error.message.clone(),
                    }
                }),
            },
            diagnostics,
        }
    }
}

fn contract_provider_health_status(status: SourceRuntimePhase) -> ReferenceProviderStatus {
    match status {
        SourceRuntimePhase::Ready | SourceRuntimePhase::Idle => ReferenceProviderStatus::Ready,
        SourceRuntimePhase::Paused => ReferenceProviderStatus::Paused,
        SourceRuntimePhase::Disabled => ReferenceProviderStatus::Disabled,
        SourceRuntimePhase::Scanning
        | SourceRuntimePhase::Promoting
        | SourceRuntimePhase::Syncing => ReferenceProviderStatus::Syncing,
        SourceRuntimePhase::Registered
        | SourceRuntimePhase::Degraded
        | SourceRuntimePhase::Unavailable => ReferenceProviderStatus::Degraded,
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct ReferenceAppWorkSummary {
    active_work_item_count: u32,
    queued_work_item_count: u32,
}

fn app_runtime_work_summary(sources: &[ReferenceSourceRuntimeStatus]) -> ReferenceAppWorkSummary {
    let active_work_item_count = sources
        .iter()
        .filter(|source| {
            matches!(
                source.phase,
                ReferenceSourcePhase::Scanning
                    | ReferenceSourcePhase::Promoting
                    | ReferenceSourcePhase::Syncing
            ) && source.work_item.work_item_id.is_some()
        })
        .count() as u32;
    let queued_work_item_count = sources
        .iter()
        .filter(|source| {
            source.retry_after_unix_nanos.is_some()
                || matches!(
                    source.work_item.skip_reason.as_deref(),
                    Some("tick_source_budget" | "tick_wall_clock_budget")
                )
        })
        .count() as u32;
    ReferenceAppWorkSummary {
        active_work_item_count,
        queued_work_item_count,
    }
}

pub(super) fn publication_batch_limit(budget: crate::domain::SourceTickBudget) -> usize {
    budget
        .max_publications_per_tick
        .filter(|value| *value > 0)
        .map(|value| value as usize)
        .unwrap_or(DEFAULT_PUBLICATION_BATCH_LIMIT)
}

fn catalog_integrity_status(model: &ReferenceReadModel) -> ReferenceCatalogIntegrityStatus {
    let missing_equity_market_count = model.missing_equity_market_count() as u64;
    let legacy_exchange_market_id_count = model.legacy_exchange_market_id_count() as u64;
    let legacy_exchange_listing_id_count = model.legacy_exchange_listing_id_count() as u64;
    ReferenceCatalogIntegrityStatus {
        degraded: missing_equity_market_count > 0
            || legacy_exchange_market_id_count > 0
            || legacy_exchange_listing_id_count > 0,
        missing_equity_market_count,
        legacy_exchange_market_id_count,
        legacy_exchange_listing_id_count,
        option_listing_count: model.option_listing_count() as u64,
        option_market_count: model.option_market_count() as u64,
    }
}

fn contract_tick_budget(budget: crate::domain::SourceTickBudget) -> ReferenceSourceTickBudget {
    ReferenceSourceTickBudget {
        max_sources_per_tick: budget.max_sources_per_tick,
        max_batches_per_source: budget.max_batches_per_source,
        max_records_per_batch: budget.max_records_per_batch,
        max_wall_clock_millis: budget.max_wall_clock_millis,
        max_publications_per_tick: budget.max_publications_per_tick,
    }
}

fn contract_app_phase(phase: ReferenceApplicationPhase) -> ReferenceAppPhase {
    match phase {
        ReferenceApplicationPhase::Booting => ReferenceAppPhase::Booting,
        ReferenceApplicationPhase::Loading => ReferenceAppPhase::Loading,
        ReferenceApplicationPhase::Serving => ReferenceAppPhase::Serving,
        ReferenceApplicationPhase::Ticking => ReferenceAppPhase::Ticking,
        ReferenceApplicationPhase::Scanning => ReferenceAppPhase::Scanning,
        ReferenceApplicationPhase::Reconciling => ReferenceAppPhase::Reconciling,
        ReferenceApplicationPhase::Publishing => ReferenceAppPhase::Publishing,
        ReferenceApplicationPhase::Degraded => ReferenceAppPhase::Degraded,
    }
}

fn contract_source_desired_state(desired_state: SourceDesiredState) -> ReferenceSourceDesiredState {
    match desired_state {
        SourceDesiredState::Enabled => ReferenceSourceDesiredState::Enabled,
        SourceDesiredState::Disabled => ReferenceSourceDesiredState::Disabled,
        SourceDesiredState::Paused => ReferenceSourceDesiredState::Paused,
        SourceDesiredState::Removed => ReferenceSourceDesiredState::Removed,
    }
}

fn contract_source_scope(scope: SourceScope) -> ReferenceSourceScope {
    ReferenceSourceScope {
        kind: match scope.kind {
            SourceScopeKind::Global => ReferenceSourceScopeKind::Global,
            SourceScopeKind::ProviderCatalog => ReferenceSourceScopeKind::ProviderCatalog,
            SourceScopeKind::UnderlyingInstrument => ReferenceSourceScopeKind::UnderlyingInstrument,
            SourceScopeKind::Coverage => ReferenceSourceScopeKind::Coverage,
            SourceScopeKind::Custom => ReferenceSourceScopeKind::Custom,
        },
        id: scope.id,
    }
}

fn contract_source_sync_policy(sync_policy: SourceSyncPolicy) -> ReferenceSourceSyncPolicy {
    match sync_policy {
        SourceSyncPolicy::FullSnapshot => ReferenceSourceSyncPolicy::FullSnapshot,
        SourceSyncPolicy::PagedSnapshot => ReferenceSourceSyncPolicy::PagedSnapshot,
        SourceSyncPolicy::ScopedSnapshot => ReferenceSourceSyncPolicy::ScopedSnapshot,
        SourceSyncPolicy::IncrementalDelta => ReferenceSourceSyncPolicy::IncrementalDelta,
        SourceSyncPolicy::ManualCurated => ReferenceSourceSyncPolicy::ManualCurated,
    }
}

fn contract_source_kind(definition: Option<&ReferenceSourceDefinition>) -> ReferenceSourceKind {
    let Some(definition) = definition else {
        return ReferenceSourceKind::Unknown;
    };
    if definition.sync_policy == SourceSyncPolicy::ManualCurated {
        return ReferenceSourceKind::ManualCurated;
    }
    if definition.sync_policy == SourceSyncPolicy::ScopedSnapshot
        || definition.scope.id.is_some()
        || !matches!(definition.scope.kind, SourceScopeKind::Global)
    {
        return ReferenceSourceKind::Scoped;
    }
    ReferenceSourceKind::Global
}

fn source_runtime_status(source: &crate::domain::SourceHealth) -> ReferenceSourceRuntimeStatus {
    let phase = match source.status {
        SourceRuntimePhase::Ready => ReferenceSourcePhase::Ready,
        SourceRuntimePhase::Idle => ReferenceSourcePhase::Idle,
        SourceRuntimePhase::Registered => ReferenceSourcePhase::Registered,
        SourceRuntimePhase::Paused => ReferenceSourcePhase::Paused,
        SourceRuntimePhase::Disabled => ReferenceSourcePhase::Disabled,
        SourceRuntimePhase::Scanning => ReferenceSourcePhase::Scanning,
        SourceRuntimePhase::Promoting => ReferenceSourcePhase::Promoting,
        SourceRuntimePhase::Syncing => ReferenceSourcePhase::Syncing,
        SourceRuntimePhase::Degraded => ReferenceSourcePhase::Degraded,
        SourceRuntimePhase::Unavailable => ReferenceSourcePhase::Unavailable,
    };
    let work_scope_present = source.work_item.scope_id.is_some()
        || source.work_item.scope_kind.is_some()
        || source.work_item.cursor_present.is_some();
    let progress_kind = if work_scope_present {
        ReferenceSourceProgressKind::Scoped
    } else {
        match source.progress.kind {
            SourceRuntimeProgressKind::Unknown => ReferenceSourceProgressKind::Unknown,
            SourceRuntimeProgressKind::Complete => ReferenceSourceProgressKind::Complete,
            SourceRuntimeProgressKind::Paged => ReferenceSourceProgressKind::Paged,
            SourceRuntimeProgressKind::Scoped => ReferenceSourceProgressKind::Scoped,
        }
    };
    let progress = ReferenceSourceProgress {
        kind: progress_kind,
        pages_done: source.progress.pages_done,
        pages_total: source.progress.pages_total,
        records_seen: source.progress.records_seen,
        records_changed: source.progress.records_changed,
        scope_id: source.work_item.scope_id.clone(),
        scope_kind: source.work_item.scope_kind.clone(),
        cursor_present: source.work_item.cursor_present,
    };
    let definition = source.definition.as_ref();
    let desired_state = definition.map(|value| value.desired_state);
    ReferenceSourceRuntimeStatus {
        source_id: source.source_id.clone(),
        provider_id: definition.map(|value| value.provider_id.clone()),
        source_kind: contract_source_kind(definition),
        configured: definition.is_some(),
        enabled: desired_state.is_none_or(|value| value == SourceDesiredState::Enabled),
        paused: desired_state == Some(SourceDesiredState::Paused),
        desired_state: desired_state.map(contract_source_desired_state),
        sync_policy: definition.map(|value| contract_source_sync_policy(value.sync_policy)),
        scope: definition.map(|value| contract_source_scope(value.scope.clone())),
        connection_id_present: definition.map(|value| value.connection_id.is_some()),
        phase,
        progress,
        work_item: ReferenceSourceWorkItem {
            work_item_id: source.work_item.work_item_id.clone(),
            scope_id: source.work_item.scope_id.clone(),
            scope_kind: source.work_item.scope_kind.clone(),
            cursor_present: source.work_item.cursor_present,
            skip_reason: source.work_item.skip_reason.clone(),
        },
        last_attempt_unix_nanos: source.last_attempt_unix_nanos,
        last_success_unix_nanos: source.last_success_unix_nanos,
        retry_after_unix_nanos: source.retry_after_unix_nanos,
        retry_backoff_seconds: source.retry_backoff_seconds,
        consecutive_failures: source.consecutive_failures,
        stale: source.stale,
        has_last_known_good: source.stale || source.last_success_unix_nanos.is_some(),
        last_error: source
            .last_error
            .as_ref()
            .map(|error| ReferenceSourceRuntimeError {
                code: error.code.clone(),
                retryable: error.retryable,
                record_kind: error.record_kind.clone(),
                record_id: error.record_id.clone(),
                message: error.message.clone(),
            }),
    }
}

fn runtime_status(
    sources: &[ReferenceSourceRuntimeStatus],
    market_count: usize,
    pending_publication_count: usize,
    catalog_integrity_degraded: bool,
) -> ReferenceRuntimeStatus {
    if market_count == 0 && sources.is_empty() {
        return ReferenceRuntimeStatus::Starting;
    }
    if sources
        .iter()
        .any(|source| matches!(source.phase, ReferenceSourcePhase::Unavailable))
    {
        return ReferenceRuntimeStatus::Unavailable;
    }
    if sources
        .iter()
        .any(|source| source.stale || matches!(source.phase, ReferenceSourcePhase::Degraded))
    {
        return ReferenceRuntimeStatus::Degraded;
    }
    if sources
        .iter()
        .any(|source| matches!(source.phase, ReferenceSourcePhase::Registered))
    {
        return ReferenceRuntimeStatus::Degraded;
    }
    if sources.iter().any(|source| {
        matches!(
            source.phase,
            ReferenceSourcePhase::Scanning
                | ReferenceSourcePhase::Promoting
                | ReferenceSourcePhase::Syncing
        )
    }) {
        return ReferenceRuntimeStatus::Syncing;
    }
    if publication_backlog_degraded(pending_publication_count) {
        return ReferenceRuntimeStatus::Degraded;
    }
    if catalog_integrity_degraded {
        return ReferenceRuntimeStatus::Degraded;
    }
    ReferenceRuntimeStatus::Ready
}

fn catalog_readiness(
    status: ReferenceRuntimeStatus,
    market_count: usize,
    catalog_integrity_degraded: bool,
) -> ReferenceCatalogReadiness {
    if catalog_integrity_degraded {
        return ReferenceCatalogReadiness::Invalid;
    }
    if market_count == 0 {
        return match status {
            ReferenceRuntimeStatus::Degraded | ReferenceRuntimeStatus::Unavailable => {
                ReferenceCatalogReadiness::Invalid
            },
            ReferenceRuntimeStatus::Starting
            | ReferenceRuntimeStatus::Syncing
            | ReferenceRuntimeStatus::Ready => ReferenceCatalogReadiness::Empty,
        };
    }
    match status {
        ReferenceRuntimeStatus::Starting | ReferenceRuntimeStatus::Syncing => {
            ReferenceCatalogReadiness::Syncing
        },
        ReferenceRuntimeStatus::Ready => ReferenceCatalogReadiness::Ready,
        ReferenceRuntimeStatus::Degraded | ReferenceRuntimeStatus::Unavailable => {
            ReferenceCatalogReadiness::Degraded
        },
    }
}
