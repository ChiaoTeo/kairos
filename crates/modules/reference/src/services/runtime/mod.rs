//! Runtime state for Reference service workflows.

mod error;
mod retry;
mod scheduler;
mod status;
mod transition;

use std::collections::{BTreeMap, BTreeSet};
use std::time::Instant;

pub(crate) use error::{source_activation_unavailable_error, source_runtime_error};
pub(crate) use scheduler::{SourceScheduleDecision, SourceScheduleSkipReason};
use status::default_source_health;

use crate::domain::{ReferenceSourceDefinition, SourceDesiredState, SourceHealth};

#[derive(Default)]
pub(crate) struct SourceRuntimeRegistry {
    definitions: BTreeMap<String, ReferenceSourceDefinition>,
    health: BTreeMap<String, SourceHealth>,
    known_last_good: BTreeSet<String>,
    inactive: BTreeSet<String>,
    retry_after: BTreeMap<String, Instant>,
}

impl SourceRuntimeRegistry {
    pub(crate) fn register_definition(&mut self, definition: ReferenceSourceDefinition) {
        if let Some(health) = self.health.get_mut(definition.source_id.as_str()) {
            health.definition = Some(definition.clone());
        }
        self.definitions
            .insert(definition.source_id.to_string(), definition);
    }

    #[cfg(test)]
    pub(crate) fn definitions(&self) -> impl Iterator<Item = &ReferenceSourceDefinition> {
        self.definitions.values()
    }

    pub(crate) fn source_ids(&self) -> impl Iterator<Item = &str> {
        self.definitions.keys().map(String::as_str)
    }

    pub(crate) fn is_inactive(&self, source_id: &str) -> bool {
        self.inactive.contains(source_id)
    }

    pub(crate) fn retry_waiting(&self, source_id: &str, now: Instant) -> bool {
        self.retry_after
            .get(source_id)
            .is_some_and(|until| *until > now)
    }

    pub(crate) fn health_for<'a>(
        &'a self,
        source_ids: impl IntoIterator<Item = &'a str>,
    ) -> Vec<SourceHealth> {
        source_ids
            .into_iter()
            .map(|source_id| {
                let mut health = self
                    .health
                    .get(source_id)
                    .cloned()
                    .unwrap_or_else(|| default_source_health(source_id));
                if health.definition.is_none() {
                    health.definition = self.definitions.get(source_id).cloned();
                }
                health
            })
            .collect()
    }

    pub(super) fn health_entry(&mut self, source_id: &str) -> &mut SourceHealth {
        self.health
            .entry(source_id.to_owned())
            .or_insert_with(|| default_source_health(source_id))
    }

    pub(super) fn set_desired_state(&mut self, source_id: &str, desired_state: SourceDesiredState) {
        if let Some(definition) = self.definitions.get_mut(source_id) {
            definition.desired_state = desired_state;
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::Instant;

    use super::{SourceRuntimeRegistry, SourceScheduleDecision, SourceScheduleSkipReason};
    use crate::domain::{
        SourceDesiredState, SourceRuntimeError, SourceRuntimePhase, SourceRuntimeProgress,
        SourceRuntimeWorkItem, SourceScope, SourceSyncPolicy, SourceTickBudget, SourceWorkItem,
        SourceWorkReason,
    };

    #[test]
    fn success_resets_failure_and_marks_last_good() {
        let mut runtime = SourceRuntimeRegistry::default();

        runtime.mark_failure("massive-options", false, None);
        runtime.mark_success(
            "massive-options",
            SourceRuntimeProgress::complete(Some(2), Some(2), Some(20), Some(5)),
            SourceRuntimeWorkItem::default(),
        );
        runtime.mark_syncing(
            "massive-options",
            SourceRuntimeProgress::paged(Some(3), Some(8), Some(30), Some(6)),
            SourceRuntimeWorkItem::default(),
        );

        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Scanning);
        assert_eq!(
            health.progress,
            SourceRuntimeProgress::paged(Some(3), Some(8), Some(30), Some(6))
        );
        assert_eq!(health.consecutive_failures, 0);
        assert!(!health.stale);
        assert!(health.last_success_unix_nanos.is_some());
        assert!(!runtime.retry_waiting("massive-options", Instant::now()));
    }

    #[test]
    fn source_definitions_are_registry_state() {
        let mut runtime = SourceRuntimeRegistry::default();

        runtime.register_definition(
            crate::services::providers::reference_source_definition("massive-options").unwrap(),
        );

        let definitions = runtime.definitions().cloned().collect::<Vec<_>>();
        assert_eq!(definitions.len(), 1);
        assert_eq!(definitions[0].source_id, "massive-options");
        assert_eq!(definitions[0].provider_id.as_str(), "massive");
        assert_eq!(definitions[0].sync_policy, SourceSyncPolicy::ScopedSnapshot);
        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(
            health
                .definition
                .as_ref()
                .map(|value| value.provider_id.as_str()),
            Some("massive")
        );
    }

    #[test]
    fn first_scan_without_last_good_is_scanning() {
        let mut runtime = SourceRuntimeRegistry::default();

        runtime.mark_syncing(
            "massive-options",
            SourceRuntimeProgress::paged(Some(1), Some(4), Some(100), None),
            SourceRuntimeWorkItem {
                work_item_id: Some("massive-options:AAPL".to_owned()),
                scope_id: Some("instrument:equity:US:AAPL:common".to_owned()),
                scope_kind: Some("underlying_instrument".to_owned()),
                cursor_present: Some(true),
                skip_reason: None,
            },
        );

        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Scanning);
        assert_eq!(
            health.progress,
            SourceRuntimeProgress::paged(Some(1), Some(4), Some(100), None)
        );
        assert_eq!(
            health.work_item.scope_id.as_deref(),
            Some("instrument:equity:US:AAPL:common")
        );
        assert_eq!(health.work_item.cursor_present, Some(true));
        assert!(!health.stale);
    }

    #[test]
    fn complete_candidate_promotes_after_reconcile_commit() {
        let mut runtime = SourceRuntimeRegistry::default();

        runtime.mark_promoting(
            "massive-options",
            SourceRuntimeProgress::complete(Some(4), Some(4), Some(400), Some(10)),
            SourceRuntimeWorkItem {
                work_item_id: Some("massive-options:AAPL".to_owned()),
                scope_id: Some("instrument:equity:US:AAPL:common".to_owned()),
                scope_kind: Some("underlying_instrument".to_owned()),
                cursor_present: Some(false),
                skip_reason: None,
            },
        );

        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Promoting);
        assert_eq!(
            health.progress,
            SourceRuntimeProgress::complete(Some(4), Some(4), Some(400), Some(10))
        );

        runtime.mark_promotions_committed();

        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Ready);
        assert_eq!(
            health.work_item.scope_id.as_deref(),
            Some("instrument:equity:US:AAPL:common")
        );
    }

    #[test]
    fn failure_with_last_good_is_stale_and_enters_retry_waiting() {
        let mut runtime = SourceRuntimeRegistry::default();

        runtime.note_last_good("massive-options");
        runtime.mark_failure(
            "massive-options",
            true,
            Some(SourceRuntimeError {
                code: "reference.provider_failed".to_owned(),
                retryable: true,
                record_kind: None,
                record_id: None,
                message: "reference provider failed: rate limited".to_owned(),
            }),
        );

        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Degraded);
        assert_eq!(health.consecutive_failures, 1);
        assert!(health.stale);
        assert!(health.retry_after_unix_nanos.is_some());
        assert_eq!(health.retry_backoff_seconds, Some(10));
        assert_eq!(
            health.last_error.as_ref().map(|error| error.code.as_str()),
            Some("reference.provider_failed")
        );
        assert!(runtime.retry_waiting("massive-options", Instant::now()));
    }

    #[test]
    fn success_clears_last_error() {
        let mut runtime = SourceRuntimeRegistry::default();

        runtime.mark_failure(
            "massive-options",
            false,
            Some(SourceRuntimeError {
                code: "reference.provider_failed".to_owned(),
                retryable: true,
                record_kind: None,
                record_id: None,
                message: "reference provider failed: timeout".to_owned(),
            }),
        );
        runtime.mark_success(
            "massive-options",
            SourceRuntimeProgress::complete(Some(1), Some(1), Some(2), None),
            SourceRuntimeWorkItem::default(),
        );

        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Ready);
        assert_eq!(health.last_error, None);
    }

    #[test]
    fn retry_policy_caps_exponential_backoff() {
        let policy = super::retry::SourceRetryPolicy::default();

        assert_eq!(policy.backoff_seconds(0), 5);
        assert_eq!(policy.backoff_seconds(1), 10);
        assert_eq!(policy.backoff_seconds(6), 300);
        assert_eq!(policy.backoff_seconds(7), 300);
    }

    #[test]
    fn pause_and_resume_are_explicit_runtime_states() {
        let mut runtime = SourceRuntimeRegistry::default();
        runtime.register_definition(
            crate::services::providers::reference_source_definition("massive-options").unwrap(),
        );

        runtime.mark_inactive("massive-options", SourceDesiredState::Paused);
        assert!(runtime.is_inactive("massive-options"));
        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Paused);
        assert_eq!(
            health.definition.map(|value| value.desired_state),
            Some(SourceDesiredState::Paused)
        );

        runtime.mark_resumed("massive-options");
        assert!(!runtime.is_inactive("massive-options"));
        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Idle);
        assert_eq!(
            health.definition.map(|value| value.desired_state),
            Some(SourceDesiredState::Enabled)
        );
    }

    #[test]
    fn durable_desired_states_mark_inactive_sources() {
        let mut runtime = SourceRuntimeRegistry::default();
        runtime.register_definition(
            crate::services::providers::reference_source_definition("massive-options").unwrap(),
        );
        runtime.register_definition(
            crate::services::providers::reference_source_definition("binance-spot").unwrap(),
        );

        runtime.set_desired_states([
            ("massive-options".to_owned(), SourceDesiredState::Disabled),
            ("binance-spot".to_owned(), SourceDesiredState::Removed),
        ]);

        assert!(runtime.is_inactive("massive-options"));
        assert!(runtime.is_inactive("binance-spot"));
        let health = runtime.health_for(["massive-options", "binance-spot"]);
        assert_eq!(health[0].status, SourceRuntimePhase::Disabled);
        assert_eq!(health[1].status, SourceRuntimePhase::Disabled);
        assert_eq!(
            health[0]
                .definition
                .as_ref()
                .map(|value| value.desired_state),
            Some(SourceDesiredState::Disabled)
        );
        assert_eq!(
            health[1]
                .definition
                .as_ref()
                .map(|value| value.desired_state),
            Some(SourceDesiredState::Removed)
        );

        runtime.set_desired_states([("massive-options".to_owned(), SourceDesiredState::Enabled)]);
        assert!(!runtime.is_inactive("massive-options"));
        assert!(runtime.is_inactive("binance-spot"));
        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Idle);
        assert_eq!(
            health.definition.as_ref().map(|value| value.desired_state),
            Some(SourceDesiredState::Enabled)
        );
    }

    #[test]
    fn apply_desired_state_owns_inactive_and_resume_transitions() {
        let mut runtime = SourceRuntimeRegistry::default();
        runtime.register_definition(
            crate::services::providers::reference_source_definition("massive-options").unwrap(),
        );

        runtime.apply_desired_state("massive-options", SourceDesiredState::Paused);
        assert!(runtime.is_inactive("massive-options"));
        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Paused);
        assert_eq!(
            health.definition.as_ref().map(|value| value.desired_state),
            Some(SourceDesiredState::Paused)
        );

        runtime.apply_desired_state("massive-options", SourceDesiredState::Enabled);
        assert!(!runtime.is_inactive("massive-options"));
        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Idle);
        assert_eq!(
            health.definition.as_ref().map(|value| value.desired_state),
            Some(SourceDesiredState::Enabled)
        );
    }

    #[test]
    fn source_health_for_active_sources_marks_registry_only_source_registered() {
        let mut runtime = SourceRuntimeRegistry::default();
        runtime.register_definition(
            crate::services::providers::reference_source_definition("binance-spot").unwrap(),
        );
        runtime.register_definition(
            crate::services::providers::reference_source_definition("massive-options").unwrap(),
        );

        let health = runtime.source_health_for_active_sources(["binance-spot"]);

        assert_eq!(health.len(), 2);
        assert_eq!(health[0].source_id, "binance-spot");
        assert_eq!(health[0].status, SourceRuntimePhase::Idle);
        assert_eq!(health[1].source_id, "massive-options");
        assert_eq!(health[1].status, SourceRuntimePhase::Registered);
    }

    #[test]
    fn enabling_source_clears_inactive_runtime_artifacts() {
        let mut runtime = SourceRuntimeRegistry::default();
        runtime.register_definition(
            crate::services::providers::reference_source_definition("massive-options").unwrap(),
        );
        runtime.mark_failure(
            "massive-options",
            false,
            Some(SourceRuntimeError {
                code: "reference.provider_failed".to_owned(),
                retryable: true,
                record_kind: None,
                record_id: None,
                message: "reference provider failed: timeout".to_owned(),
            }),
        );
        runtime.mark_scheduled(&SourceWorkItem {
            work_item_id: "massive-options:global".to_owned(),
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                .unwrap(),
            scope: SourceScope::default(),
            reason: SourceWorkReason::Retry,
            budget: SourceTickBudget::default(),
        });
        runtime.set_desired_states([("massive-options".to_owned(), SourceDesiredState::Disabled)]);

        runtime.set_desired_states([("massive-options".to_owned(), SourceDesiredState::Enabled)]);

        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.status, SourceRuntimePhase::Idle);
        assert_eq!(health.progress, SourceRuntimeProgress::unknown());
        assert_eq!(health.work_item, SourceRuntimeWorkItem::default());
        assert_eq!(health.retry_after_unix_nanos, None);
        assert_eq!(health.retry_backoff_seconds, None);
        assert_eq!(health.last_error, None);
        assert!(!runtime.retry_waiting("massive-options", Instant::now()));
    }

    #[test]
    fn scheduler_skips_inactive_and_retry_waiting_sources() {
        let mut runtime = SourceRuntimeRegistry::default();
        runtime.register_definition(
            crate::services::providers::reference_source_definition("massive-options").unwrap(),
        );

        runtime.mark_inactive("massive-options", SourceDesiredState::Paused);
        assert_eq!(
            runtime.schedule_source_tick(
                "massive-options",
                Instant::now(),
                SourceTickBudget::default()
            ),
            SourceScheduleDecision::Skipped(SourceScheduleSkipReason::Inactive)
        );

        runtime.mark_resumed("massive-options");
        runtime.mark_failure("massive-options", true, None);
        assert_eq!(
            runtime.schedule_source_tick(
                "massive-options",
                Instant::now(),
                SourceTickBudget::default()
            ),
            SourceScheduleDecision::Skipped(SourceScheduleSkipReason::RetryWaiting)
        );
    }

    #[test]
    fn scheduler_returns_source_work_item_after_retry_window() {
        let mut runtime = SourceRuntimeRegistry::default();
        let mut definition =
            crate::services::providers::reference_source_definition("massive-options").unwrap();
        definition.scope = SourceScope::underlying_instrument("instrument:equity:US:SPY:common");
        runtime.register_definition(definition);
        runtime.mark_failure("massive-options", true, None);

        let decision = runtime.schedule_source_tick(
            "massive-options",
            Instant::now() + std::time::Duration::from_secs(600),
            SourceTickBudget {
                max_sources_per_tick: 2,
                ..SourceTickBudget::default()
            },
        );

        let SourceScheduleDecision::Scheduled(work_item) = decision else {
            panic!("retry source should become schedulable after retry window");
        };
        assert_eq!(work_item.source_id, "massive-options");
        assert_eq!(work_item.reason, SourceWorkReason::Retry);
        assert_eq!(
            work_item.scope.kind,
            crate::domain::SourceScopeKind::UnderlyingInstrument
        );
        assert_eq!(
            work_item.scope.id.as_deref(),
            Some("instrument:equity:US:SPY:common")
        );
        assert_eq!(work_item.budget.max_sources_per_tick, 2);
        assert_eq!(
            work_item.work_item_id,
            "massive-options:underlying_instrument:instrument:equity:US:SPY:common"
        );
    }

    #[test]
    fn scheduled_work_item_is_visible_in_runtime_health() {
        let mut runtime = SourceRuntimeRegistry::default();
        let mut definition =
            crate::services::providers::reference_source_definition("massive-options").unwrap();
        definition.scope = SourceScope::underlying_instrument("instrument:equity:US:SPY:common");
        runtime.register_definition(definition);

        let SourceScheduleDecision::Scheduled(work_item) = runtime.schedule_source_tick(
            "massive-options",
            Instant::now(),
            SourceTickBudget::default(),
        ) else {
            panic!("enabled source should be schedulable");
        };
        runtime.mark_scheduled(&work_item);

        let health = runtime.health_for(["massive-options"])[0].clone();
        assert!(health.last_attempt_unix_nanos.is_some());
        assert_eq!(
            health.work_item.work_item_id.as_deref(),
            Some("massive-options:underlying_instrument:instrument:equity:US:SPY:common")
        );
        assert_eq!(
            health.work_item.scope_id.as_deref(),
            Some("instrument:equity:US:SPY:common")
        );
        assert_eq!(
            health.work_item.scope_kind.as_deref(),
            Some("underlying_instrument")
        );
        assert_eq!(health.work_item.cursor_present, None);
    }

    #[test]
    fn deferred_work_item_exposes_skip_reason_without_attempt() {
        let mut runtime = SourceRuntimeRegistry::default();
        let mut definition =
            crate::services::providers::reference_source_definition("massive-options").unwrap();
        definition.scope = SourceScope::underlying_instrument("instrument:equity:US:SPY:common");
        runtime.register_definition(definition);

        let SourceScheduleDecision::Scheduled(work_item) = runtime.schedule_source_tick(
            "massive-options",
            Instant::now(),
            SourceTickBudget::default(),
        ) else {
            panic!("enabled source should be schedulable");
        };
        runtime.mark_deferred(&work_item, SourceScheduleSkipReason::TickSourceBudget);

        let health = runtime.health_for(["massive-options"])[0].clone();
        assert_eq!(health.last_attempt_unix_nanos, None);
        assert_eq!(
            health.work_item.work_item_id.as_deref(),
            Some("massive-options:underlying_instrument:instrument:equity:US:SPY:common")
        );
        assert_eq!(
            health.work_item.skip_reason.as_deref(),
            Some("tick_source_budget")
        );
    }

    #[test]
    fn targeted_refresh_work_item_uses_rpc_refresh_reason() {
        let mut runtime = SourceRuntimeRegistry::default();
        let mut definition =
            crate::services::providers::reference_source_definition("massive-options").unwrap();
        definition.scope = SourceScope::underlying_instrument("instrument:equity:US:SPY:common");
        runtime.register_definition(definition);

        let SourceScheduleDecision::Scheduled(work_item) = runtime.schedule_source_refresh(
            "massive-options",
            Instant::now(),
            SourceTickBudget {
                max_batches_per_source: 3,
                ..SourceTickBudget::default()
            },
        ) else {
            panic!("enabled source should be refreshable");
        };

        assert_eq!(work_item.reason, SourceWorkReason::RpcRefresh);
        assert_eq!(work_item.budget.max_batches_per_source, 3);
        assert_eq!(
            work_item.scope.id.as_deref(),
            Some("instrument:equity:US:SPY:common")
        );
        assert_eq!(
            work_item.work_item_id,
            "massive-options:underlying_instrument:instrument:equity:US:SPY:common"
        );
    }

    #[test]
    fn source_refresh_work_item_maps_skip_reasons_to_control_results() {
        let mut runtime = SourceRuntimeRegistry::default();
        runtime.register_definition(
            crate::services::providers::reference_source_definition("massive-options").unwrap(),
        );

        runtime.mark_inactive("massive-options", SourceDesiredState::Paused);
        assert!(
            runtime
                .source_refresh_work_item(
                    "massive-options",
                    Instant::now(),
                    SourceTickBudget::default()
                )
                .unwrap()
                .is_none()
        );

        runtime.mark_resumed("massive-options");
        runtime.mark_failure("massive-options", true, None);
        let error = runtime
            .source_refresh_work_item(
                "massive-options",
                Instant::now(),
                SourceTickBudget::default(),
            )
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("massive-options: provider retry window is waiting")
        );
    }

    #[test]
    fn registered_source_without_adapter_error_distinguishes_unknown_source() {
        let mut runtime = SourceRuntimeRegistry::default();
        runtime.register_definition(
            crate::services::providers::reference_source_definition("massive-options").unwrap(),
        );

        let registered = runtime.registered_source_without_adapter_error("massive-options");
        assert!(
            registered
                .to_string()
                .contains("registered but has no active runtime adapter")
        );

        let unknown = runtime.registered_source_without_adapter_error("missing-source");
        assert!(unknown.to_string().contains("unknown reference source"));
    }
}
