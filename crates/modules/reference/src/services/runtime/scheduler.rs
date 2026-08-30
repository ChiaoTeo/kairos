//! Source scheduler decisions for Reference runtime.

use std::time::Instant;

use super::SourceRuntimeRegistry;
use crate::domain::{
    ReferenceError, ReferenceResult, SourceTickBudget, SourceWorkItem, SourceWorkReason,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum SourceScheduleDecision {
    Scheduled(SourceWorkItem),
    Skipped(SourceScheduleSkipReason),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum SourceScheduleSkipReason {
    Inactive,
    RetryWaiting,
    TickSourceBudget,
    TickWallClockBudget,
}

impl SourceScheduleSkipReason {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Inactive => "inactive",
            Self::RetryWaiting => "retry_waiting",
            Self::TickSourceBudget => "tick_source_budget",
            Self::TickWallClockBudget => "tick_wall_clock_budget",
        }
    }

    pub(crate) fn refresh_error(self, source_id: &str) -> ReferenceError {
        match self {
            Self::Inactive => {
                ReferenceError::Provider(format!("{source_id}: source refresh skipped: inactive"))
            },
            Self::RetryWaiting => {
                ReferenceError::Provider(format!("{source_id}: provider retry window is waiting"))
            },
            Self::TickSourceBudget | Self::TickWallClockBudget => ReferenceError::Provider(
                format!("{source_id}: source refresh skipped: {}", self.as_str()),
            ),
        }
    }
}

impl SourceRuntimeRegistry {
    pub(crate) fn schedule_source_tick(
        &self,
        source_id: &str,
        now: Instant,
        budget: SourceTickBudget,
    ) -> SourceScheduleDecision {
        if self.is_inactive(source_id) {
            return SourceScheduleDecision::Skipped(SourceScheduleSkipReason::Inactive);
        }
        if self.retry_waiting(source_id, now) {
            return SourceScheduleDecision::Skipped(SourceScheduleSkipReason::RetryWaiting);
        }
        let reason = self
            .health
            .get(source_id)
            .filter(|health| health.consecutive_failures > 0)
            .map(|_| SourceWorkReason::Retry)
            .unwrap_or(SourceWorkReason::ScheduledTick);
        SourceScheduleDecision::Scheduled(self.source_work_item(source_id, reason, budget))
    }

    pub(crate) fn schedule_source_refresh(
        &self,
        source_id: &str,
        now: Instant,
        budget: SourceTickBudget,
    ) -> SourceScheduleDecision {
        if self.is_inactive(source_id) {
            return SourceScheduleDecision::Skipped(SourceScheduleSkipReason::Inactive);
        }
        if self.retry_waiting(source_id, now) {
            return SourceScheduleDecision::Skipped(SourceScheduleSkipReason::RetryWaiting);
        }
        SourceScheduleDecision::Scheduled(self.source_work_item(
            source_id,
            SourceWorkReason::RpcRefresh,
            budget,
        ))
    }

    pub(crate) fn registered_source_without_adapter_error(
        &self,
        source_id: &str,
    ) -> ReferenceError {
        if self.source_ids().any(|known| known == source_id) {
            ReferenceError::Invalid(format!(
                "reference source is registered but has no active runtime adapter: {source_id}"
            ))
        } else {
            ReferenceError::Invalid(format!("unknown reference source: {source_id}"))
        }
    }

    pub(crate) fn source_refresh_work_item(
        &self,
        source_id: &str,
        now: Instant,
        budget: SourceTickBudget,
    ) -> ReferenceResult<Option<SourceWorkItem>> {
        match self.schedule_source_refresh(source_id, now, budget) {
            SourceScheduleDecision::Scheduled(work_item) => Ok(Some(work_item)),
            SourceScheduleDecision::Skipped(SourceScheduleSkipReason::Inactive) => Ok(None),
            SourceScheduleDecision::Skipped(reason) => Err(reason.refresh_error(source_id)),
        }
    }

    fn source_work_item(
        &self,
        source_id: &str,
        reason: SourceWorkReason,
        budget: SourceTickBudget,
    ) -> SourceWorkItem {
        let definition = self.definitions.get(source_id);
        let scope = definition
            .map(|definition| definition.scope.clone())
            .unwrap_or_default();
        SourceWorkItem {
            work_item_id: source_work_item_id(source_id, &scope),
            source_id: kairos_primitives::reference::ReferenceSourceId::new(source_id)
                .expect("registered Reference source identity is validated"),
            scope,
            reason,
            budget,
        }
    }
}

fn source_work_item_id(source_id: &str, scope: &crate::domain::SourceScope) -> String {
    match scope.id.as_deref() {
        Some(scope_id) => format!("{source_id}:{}:{scope_id}", scope.kind.as_str()),
        None => format!("{source_id}:{}", scope.kind.as_str()),
    }
}
