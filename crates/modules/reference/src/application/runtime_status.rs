use kairos_primitives::integration::ProviderId;
use kairos_primitives::reference::InstrumentId;
use kairos_reference_contract::{
    ReferenceAppPhase, ReferenceAppRuntimeError, ReferenceAppRuntimeStatus,
    ReferenceCatalogIntegrityStatus, ReferenceCatalogReadiness, ReferenceCatalogRuntimeStatus,
    ReferenceCoverageRuntimeStatus, ReferenceHealthResponse, ReferenceHealthStatus,
    ReferenceProviderHealth, ReferenceProviderProduct, ReferenceProviderStatus,
    ReferencePublicationRuntimeError, ReferencePublicationRuntimeStatus, ReferenceRuntimeStatus,
    ReferenceRuntimeStatusResponse, ReferenceSourceDesiredState, ReferenceSourceKind,
    ReferenceSourcePhase, ReferenceSourceProgress, ReferenceSourceProgressKind,
    ReferenceSourceRuntimeError, ReferenceSourceRuntimeStatus, ReferenceSourceScope,
    ReferenceSourceScopeKind, ReferenceSourceSyncPolicy, ReferenceSourceTickBudget,
    ReferenceSourceWorkItem,
};

use crate::application::diagnostics::{publication_backlog_degraded, runtime_diagnostics};
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
                source_id: ProviderId::new(provider.source_id.clone())
                    .expect("normalized provider source identity"),
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
                actor_id: model.actor_id().to_owned(),
                source_id: model.source_id().to_owned(),
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
                entity_count: model.entity_count() as u64,
                asset_count: model.asset_count() as u64,
                instrument_count: model.instrument_count() as u64,
                listing_count: model.listing_count() as u64,
                market_count: model.market_count() as u64,
                active_market_count: model.active_market_count() as u64,
                lifecycle_event_count: model.lifecycle_event_count() as u64,
                integrity: catalog_integrity,
            },
            sources,
            coverage: ReferenceCoverageRuntimeStatus {
                option_underlyings: self
                    .option_underlyings()
                    .into_iter()
                    .filter_map(|value| InstrumentId::new(value).ok())
                    .collect(),
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

fn contract_provider_product(provider_product: &str) -> ReferenceProviderProduct {
    match provider_product.trim().to_ascii_lowercase().as_str() {
        "spot" => ReferenceProviderProduct::Spot,
        "equity" => ReferenceProviderProduct::Equity,
        "options" | "option" => ReferenceProviderProduct::Options,
        "usdm" | "usd-m" | "usd_m" | "usdm-futures" | "usd-m-futures" => {
            ReferenceProviderProduct::Usdm
        },
        "coinm" | "coin-m" | "coin_m" | "coinm-futures" | "coin-m-futures" => {
            ReferenceProviderProduct::Coinm
        },
        "margin" => ReferenceProviderProduct::Margin,
        "swap" => ReferenceProviderProduct::Swap,
        "futures" => ReferenceProviderProduct::Futures,
        "perpetual" => ReferenceProviderProduct::Perpetual,
        _ => ReferenceProviderProduct::Unknown,
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
        source_id: ProviderId::new(source.source_id.clone())
            .expect("normalized reference source identity"),
        provider_id: definition.map(|value| value.provider_id.clone()),
        provider_product: definition
            .and_then(|value| value.provider_product.as_deref())
            .map(contract_provider_product),
        source_kind: contract_source_kind(definition),
        configured: definition.is_some(),
        enabled: desired_state.is_none_or(|value| value == SourceDesiredState::Enabled),
        paused: desired_state == Some(SourceDesiredState::Paused),
        desired_state: desired_state.map(contract_source_desired_state),
        sync_policy: definition.map(|value| contract_source_sync_policy(value.sync_policy)),
        scope: definition.map(|value| contract_source_scope(value.scope.clone())),
        credential_binding_present: definition.map(|value| value.credential_binding.is_some()),
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

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ProviderId;
    use kairos_reference_contract::{
        ReferenceAppPhase, ReferenceCatalogIntegrityStatus, ReferenceCatalogReadiness,
        ReferenceProviderProduct, ReferenceRuntimeStatus, ReferenceSourceDesiredState,
        ReferenceSourceKind, ReferenceSourcePhase, ReferenceSourceProgress,
        ReferenceSourceProgressKind, ReferenceSourceRuntimeStatus, ReferenceSourceScopeKind,
        ReferenceSourceSyncPolicy, ReferenceSourceWorkItem,
    };

    use crate::application::ReferenceApplicationPhase;
    use crate::domain::{
        ReferenceSourceDefinition, SourceDesiredState, SourceHealth, SourceRuntimeError,
        SourceRuntimePhase, SourceRuntimeProgress, SourceRuntimeWorkItem, SourceSyncPolicy,
    };

    use super::{
        DEFAULT_PUBLICATION_BATCH_LIMIT, app_runtime_work_summary, catalog_readiness,
        contract_app_phase, contract_tick_budget, publication_batch_limit, runtime_status,
        source_runtime_status,
    };

    fn source_status(
        source_id: &str,
        phase: ReferenceSourcePhase,
        has_last_known_good: bool,
    ) -> ReferenceSourceRuntimeStatus {
        ReferenceSourceRuntimeStatus {
            source_id: ProviderId::new(source_id.to_owned()).unwrap(),
            provider_id: None,
            provider_product: None,
            source_kind: ReferenceSourceKind::Unknown,
            configured: false,
            enabled: true,
            paused: false,
            desired_state: None,
            sync_policy: None,
            scope: None,
            credential_binding_present: None,
            phase,
            progress: ReferenceSourceProgress {
                kind: ReferenceSourceProgressKind::Unknown,
                pages_done: None,
                pages_total: None,
                records_seen: None,
                records_changed: None,
                scope_id: None,
                scope_kind: None,
                cursor_present: None,
            },
            work_item: ReferenceSourceWorkItem::default(),
            last_attempt_unix_nanos: None,
            last_success_unix_nanos: None,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: None,
            consecutive_failures: 0,
            stale: false,
            has_last_known_good,
            last_error: None,
        }
    }

    #[test]
    fn runtime_status_treats_disabled_source_as_ready_when_catalog_exists() {
        let mut disabled = source_status("massive-options", ReferenceSourcePhase::Disabled, true);
        disabled.desired_state = Some(ReferenceSourceDesiredState::Disabled);

        assert_eq!(
            runtime_status(&[disabled], 1, 0, false),
            ReferenceRuntimeStatus::Ready
        );
    }

    #[test]
    fn runtime_status_treats_removed_source_as_ready_when_catalog_exists() {
        let mut removed = source_status("massive-options", ReferenceSourcePhase::Disabled, true);
        removed.desired_state = Some(ReferenceSourceDesiredState::Removed);

        assert_eq!(
            runtime_status(&[removed], 1, 0, false),
            ReferenceRuntimeStatus::Ready
        );
    }

    #[test]
    fn app_runtime_work_summary_counts_active_and_retry_waiting_sources() {
        let mut syncing = source_status("massive-options", ReferenceSourcePhase::Scanning, false);
        syncing.work_item = ReferenceSourceWorkItem {
            work_item_id: Some(
                "massive-options:underlying_instrument:instrument:equity:US:SPY:common".into(),
            ),
            scope_id: Some("instrument:equity:US:SPY:common".into()),
            scope_kind: Some("underlying_instrument".into()),
            cursor_present: Some(true),
            skip_reason: None,
        };
        let mut retry_waiting = source_status("binance-spot", ReferenceSourcePhase::Degraded, true);
        retry_waiting.retry_after_unix_nanos = Some(123.into());
        let mut budget_deferred =
            source_status("massive-equity", ReferenceSourcePhase::Idle, false);
        budget_deferred.work_item = ReferenceSourceWorkItem {
            work_item_id: Some("massive-equity:provider_catalog".into()),
            scope_id: Some("massive-equity".into()),
            scope_kind: Some("provider_catalog".into()),
            cursor_present: None,
            skip_reason: Some("tick_source_budget".into()),
        };

        let summary = app_runtime_work_summary(&[syncing, retry_waiting, budget_deferred]);

        assert_eq!(summary.active_work_item_count, 1);
        assert_eq!(summary.queued_work_item_count, 2);
    }

    #[test]
    fn source_runtime_status_maps_registered_source_phase() {
        let status = source_runtime_status(&SourceHealth {
            source_id: "massive-options".to_owned(),
            definition: Some(ReferenceSourceDefinition::from_source_id("massive-options")),
            status: SourceRuntimePhase::Registered,
            progress: SourceRuntimeProgress::unknown(),
            work_item: Default::default(),
            last_attempt_unix_nanos: None,
            last_success_unix_nanos: None,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: None,
            consecutive_failures: 0,
            stale: false,
            last_error: None,
        });

        assert_eq!(status.phase, ReferenceSourcePhase::Registered);
        assert_eq!(
            status.scope.as_ref().map(|scope| scope.kind),
            Some(ReferenceSourceScopeKind::Global)
        );
        assert_eq!(
            runtime_status(&[status], 1, 0, false),
            ReferenceRuntimeStatus::Degraded
        );
    }

    #[test]
    fn source_runtime_status_maps_idle_source_phase() {
        let status = source_runtime_status(&SourceHealth {
            source_id: "binance-spot".to_owned(),
            definition: Some(ReferenceSourceDefinition::from_source_id("binance-spot")),
            status: SourceRuntimePhase::Idle,
            progress: SourceRuntimeProgress::unknown(),
            work_item: Default::default(),
            last_attempt_unix_nanos: None,
            last_success_unix_nanos: None,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: None,
            consecutive_failures: 0,
            stale: false,
            last_error: None,
        });

        assert_eq!(status.phase, ReferenceSourcePhase::Idle);
        assert!(!status.has_last_known_good);
    }

    #[test]
    fn runtime_status_degrades_when_publication_backlog_exists() {
        let status = source_status("binance-spot", ReferenceSourcePhase::Ready, true);

        assert_eq!(
            runtime_status(std::slice::from_ref(&status), 1, 0, false),
            ReferenceRuntimeStatus::Ready
        );
        assert_eq!(
            runtime_status(&[status], 1, 1, false),
            ReferenceRuntimeStatus::Degraded
        );
    }

    #[test]
    fn runtime_status_degrades_on_catalog_integrity() {
        let status = source_status("binance-spot", ReferenceSourcePhase::Ready, true);
        let integrity = ReferenceCatalogIntegrityStatus {
            degraded: true,
            missing_equity_market_count: 1,
            legacy_exchange_market_id_count: 2,
            legacy_exchange_listing_id_count: 3,
            option_listing_count: 4,
            option_market_count: 5,
        };

        assert_eq!(
            runtime_status(std::slice::from_ref(&status), 1, 0, integrity.degraded),
            ReferenceRuntimeStatus::Degraded
        );
        assert_eq!(
            catalog_readiness(ReferenceRuntimeStatus::Degraded, 1, integrity.degraded),
            ReferenceCatalogReadiness::Invalid
        );
    }

    #[test]
    fn catalog_readiness_distinguishes_empty_from_invalid() {
        assert_eq!(
            catalog_readiness(ReferenceRuntimeStatus::Starting, 0, false),
            ReferenceCatalogReadiness::Empty
        );
        assert_eq!(
            catalog_readiness(ReferenceRuntimeStatus::Unavailable, 0, false),
            ReferenceCatalogReadiness::Invalid
        );
        assert_eq!(
            catalog_readiness(ReferenceRuntimeStatus::Degraded, 1, false),
            ReferenceCatalogReadiness::Degraded
        );
    }

    #[test]
    fn source_runtime_status_maps_normalized_builtin_products() {
        let cases = [
            ("binance-usdm-futures", ReferenceProviderProduct::Usdm),
            ("binance-coinm-futures", ReferenceProviderProduct::Coinm),
            ("hyperliquid-perpetual", ReferenceProviderProduct::Perpetual),
            ("massive-equity", ReferenceProviderProduct::Equity),
            ("massive-options", ReferenceProviderProduct::Options),
        ];

        for (source_id, expected_product) in cases {
            let status = source_runtime_status(&SourceHealth {
                source_id: source_id.to_owned(),
                definition: Some(ReferenceSourceDefinition::from_source_id(source_id)),
                status: SourceRuntimePhase::Registered,
                progress: SourceRuntimeProgress::unknown(),
                work_item: Default::default(),
                last_attempt_unix_nanos: None,
                last_success_unix_nanos: None,
                retry_after_unix_nanos: None,
                retry_backoff_seconds: None,
                consecutive_failures: 0,
                stale: false,
                last_error: None,
            });
            assert_eq!(status.provider_product, Some(expected_product));
        }
    }

    #[test]
    fn source_runtime_status_includes_source_definition() {
        let status = source_runtime_status(&SourceHealth {
            source_id: "massive-options".to_owned(),
            definition: Some(ReferenceSourceDefinition {
                source_id: kairos_primitives::integration::ProviderId::new("massive-options")
                    .unwrap(),
                provider_id: kairos_primitives::integration::ProviderId::new("massive").unwrap(),
                provider_product: Some(
                    kairos_primitives::integration::ProviderProductCode::new("options").unwrap(),
                ),
                scope: Default::default(),
                desired_state: SourceDesiredState::Paused,
                credential_binding: None,
                sync_policy: SourceSyncPolicy::ScopedSnapshot,
            }),
            status: SourceRuntimePhase::Paused,
            progress: SourceRuntimeProgress::unknown(),
            work_item: Default::default(),
            last_attempt_unix_nanos: None,
            last_success_unix_nanos: None,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: None,
            consecutive_failures: 0,
            stale: false,
            last_error: None,
        });

        assert_eq!(
            status.provider_id,
            Some(ProviderId::new("massive").unwrap())
        );
        assert_eq!(
            status.provider_product,
            Some(ReferenceProviderProduct::Options)
        );
        assert_eq!(status.source_kind, ReferenceSourceKind::Scoped);
        assert!(status.configured);
        assert!(!status.enabled);
        assert!(status.paused);
        assert_eq!(
            status.desired_state,
            Some(ReferenceSourceDesiredState::Paused)
        );
        assert_eq!(
            status.sync_policy,
            Some(ReferenceSourceSyncPolicy::ScopedSnapshot)
        );
    }

    #[test]
    fn source_runtime_status_maps_disabled_runtime_phase() {
        let status = source_runtime_status(&SourceHealth {
            source_id: "massive-options".to_owned(),
            definition: Some(ReferenceSourceDefinition {
                source_id: kairos_primitives::integration::ProviderId::new("massive-options")
                    .unwrap(),
                provider_id: kairos_primitives::integration::ProviderId::new("massive").unwrap(),
                provider_product: Some(
                    kairos_primitives::integration::ProviderProductCode::new("options").unwrap(),
                ),
                scope: Default::default(),
                desired_state: SourceDesiredState::Disabled,
                credential_binding: None,
                sync_policy: SourceSyncPolicy::ScopedSnapshot,
            }),
            status: SourceRuntimePhase::Disabled,
            progress: SourceRuntimeProgress::unknown(),
            work_item: Default::default(),
            last_attempt_unix_nanos: None,
            last_success_unix_nanos: None,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: None,
            consecutive_failures: 0,
            stale: false,
            last_error: None,
        });

        assert_eq!(
            status.desired_state,
            Some(ReferenceSourceDesiredState::Disabled)
        );
        assert_eq!(status.phase, ReferenceSourcePhase::Disabled);
    }

    #[test]
    fn source_runtime_status_exposes_only_opaque_cursor_presence() {
        let status = source_runtime_status(&SourceHealth {
            source_id: "massive-equity".to_owned(),
            definition: Some(ReferenceSourceDefinition::from_source_id("massive-equity")),
            status: SourceRuntimePhase::Scanning,
            progress: SourceRuntimeProgress::paged(Some(3), None, Some(3_000), Some(120)),
            work_item: SourceRuntimeWorkItem {
                work_item_id: Some("massive-equity:provider_catalog".to_owned()),
                scope_id: Some("massive-equity".to_owned()),
                scope_kind: Some("provider_catalog".to_owned()),
                cursor_present: Some(true),
                skip_reason: None,
            },
            last_attempt_unix_nanos: None,
            last_success_unix_nanos: None,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: None,
            consecutive_failures: 0,
            stale: false,
            last_error: None,
        });

        assert_eq!(status.work_item.cursor_present, Some(true));
        assert_eq!(status.progress.kind, ReferenceSourceProgressKind::Scoped);
        assert_eq!(status.progress.scope_id.as_deref(), Some("massive-equity"));
        assert_eq!(
            status.progress.scope_kind.as_deref(),
            Some("provider_catalog")
        );
        assert_eq!(status.progress.cursor_present, Some(true));
        let value = serde_json::to_value(&status).unwrap();
        assert_eq!(value["work_item"]["cursor_present"], true);
        assert!(value["work_item"].get("cursor").is_none());
        assert_eq!(value["progress"]["kind"], "scoped");
        assert_eq!(value["progress"]["cursor_present"], true);
        assert!(value["progress"].get("cursor").is_none());
        assert!(value.to_string().find("page-").is_none());
    }

    #[test]
    fn source_runtime_status_includes_last_error_summary() {
        let status = source_runtime_status(&SourceHealth {
            source_id: "binance-spot".to_owned(),
            definition: Some(ReferenceSourceDefinition::from_source_id("binance-spot")),
            status: SourceRuntimePhase::Unavailable,
            progress: SourceRuntimeProgress::unknown(),
            work_item: Default::default(),
            last_attempt_unix_nanos: None,
            last_success_unix_nanos: None,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: Some(10),
            consecutive_failures: 1,
            stale: false,
            last_error: Some(SourceRuntimeError {
                code: "reference.provider_failed".to_owned(),
                retryable: true,
                record_kind: None,
                record_id: None,
                message: "reference provider failed: HTTP 429".to_owned(),
            }),
        });

        assert_eq!(
            status.last_error.as_ref().map(|error| error.code.as_str()),
            Some("reference.provider_failed")
        );
        assert_eq!(
            status
                .last_error
                .as_ref()
                .map(|error| error.message.as_str()),
            Some("reference provider failed: HTTP 429")
        );
    }

    #[test]
    fn app_runtime_status_exposes_tick_budget_shape() {
        let budget = contract_tick_budget(crate::domain::SourceTickBudget::default());

        assert_eq!(budget.max_sources_per_tick, 1);
        assert_eq!(budget.max_batches_per_source, 1);
        assert_eq!(budget.max_records_per_batch, None);
        assert_eq!(budget.max_wall_clock_millis, None);
        assert_eq!(budget.max_publications_per_tick, None);
    }

    #[test]
    fn app_phase_contract_mapping_covers_state_machine() {
        assert_eq!(
            contract_app_phase(ReferenceApplicationPhase::Booting),
            ReferenceAppPhase::Booting
        );
        assert_eq!(
            contract_app_phase(ReferenceApplicationPhase::Loading),
            ReferenceAppPhase::Loading
        );
        assert_eq!(
            contract_app_phase(ReferenceApplicationPhase::Serving),
            ReferenceAppPhase::Serving
        );
        assert_eq!(
            contract_app_phase(ReferenceApplicationPhase::Ticking),
            ReferenceAppPhase::Ticking
        );
        assert_eq!(
            contract_app_phase(ReferenceApplicationPhase::Scanning),
            ReferenceAppPhase::Scanning
        );
        assert_eq!(
            contract_app_phase(ReferenceApplicationPhase::Reconciling),
            ReferenceAppPhase::Reconciling
        );
        assert_eq!(
            contract_app_phase(ReferenceApplicationPhase::Publishing),
            ReferenceAppPhase::Publishing
        );
        assert_eq!(
            contract_app_phase(ReferenceApplicationPhase::Degraded),
            ReferenceAppPhase::Degraded
        );
    }

    #[test]
    fn publication_batch_limit_uses_tick_budget_when_configured() {
        assert_eq!(
            publication_batch_limit(crate::domain::SourceTickBudget::default()),
            DEFAULT_PUBLICATION_BATCH_LIMIT
        );

        assert_eq!(
            publication_batch_limit(crate::domain::SourceTickBudget {
                max_publications_per_tick: Some(25),
                ..crate::domain::SourceTickBudget::default()
            }),
            25
        );
    }
}
