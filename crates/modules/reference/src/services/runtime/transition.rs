//! Source runtime phase transitions.

use super::SourceRuntimeRegistry;
use super::retry::{SourceRetryPolicy, retry_after_unix_nanos};
use super::scheduler::SourceScheduleSkipReason;
use super::status::inactive_runtime_phase;
use crate::domain::{
    SourceDesiredState, SourceRuntimeError, SourceRuntimePhase, SourceRuntimeProgress,
    SourceRuntimeWorkItem, SourceWorkItem,
};

impl SourceRuntimeRegistry {
    pub(crate) fn mark_registration_error(&mut self, source_id: &str, error: SourceRuntimeError) {
        let health = self.health_entry(source_id);
        health.status = SourceRuntimePhase::Registered;
        health.last_error = Some(error);
        health.stale = false;
        health.retry_after_unix_nanos = None;
        health.retry_backoff_seconds = None;
    }

    pub(crate) fn note_last_good(&mut self, source_id: impl Into<String>) {
        self.known_last_good.insert(source_id.into());
    }

    pub(crate) fn set_desired_states(
        &mut self,
        desired_states: impl IntoIterator<Item = (String, SourceDesiredState)>,
    ) {
        for (source_id, desired_state) in desired_states {
            self.apply_desired_state(&source_id, desired_state);
        }
    }

    pub(crate) fn apply_desired_state(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
    ) {
        if matches!(
            desired_state,
            SourceDesiredState::Paused | SourceDesiredState::Disabled | SourceDesiredState::Removed
        ) {
            self.mark_inactive(source_id, desired_state);
        } else {
            self.mark_resumed(source_id);
        }
    }

    pub(crate) fn mark_scheduled(&mut self, work_item: &SourceWorkItem) {
        let health = self.health_entry(&work_item.source_id);
        health.last_attempt_unix_nanos = Some(crate::services::time::unix_nanos());
        health.work_item = work_item.runtime_snapshot(None);
    }

    pub(crate) fn mark_deferred(
        &mut self,
        work_item: &SourceWorkItem,
        reason: SourceScheduleSkipReason,
    ) {
        let health = self.health_entry(&work_item.source_id);
        let mut snapshot = work_item.runtime_snapshot(None);
        snapshot.skip_reason = Some(reason.as_str().to_owned());
        health.work_item = snapshot;
    }

    #[cfg(test)]
    pub(crate) fn mark_attempt(&mut self, source_id: &str) {
        let health = self.health_entry(source_id);
        health.last_attempt_unix_nanos = Some(crate::services::time::unix_nanos());
    }

    #[cfg(test)]
    pub(crate) fn mark_success(
        &mut self,
        source_id: &str,
        progress: SourceRuntimeProgress,
        work_item: SourceRuntimeWorkItem,
    ) {
        let health = self.health_entry(source_id);
        health.status = SourceRuntimePhase::Ready;
        health.progress = progress;
        health.work_item = work_item;
        health.last_success_unix_nanos = Some(crate::services::time::unix_nanos());
        health.retry_after_unix_nanos = None;
        health.retry_backoff_seconds = None;
        health.consecutive_failures = 0;
        health.stale = false;
        health.last_error = None;
        self.known_last_good.insert(source_id.to_owned());
        self.retry_after.remove(source_id);
    }

    pub(crate) fn mark_promoting(
        &mut self,
        source_id: &str,
        progress: SourceRuntimeProgress,
        work_item: SourceRuntimeWorkItem,
    ) {
        let health = self.health_entry(source_id);
        health.status = SourceRuntimePhase::Promoting;
        health.progress = progress;
        health.work_item = work_item;
        health.last_success_unix_nanos = Some(crate::services::time::unix_nanos());
        health.retry_after_unix_nanos = None;
        health.retry_backoff_seconds = None;
        health.consecutive_failures = 0;
        health.stale = false;
        health.last_error = None;
        self.known_last_good.insert(source_id.to_owned());
        self.retry_after.remove(source_id);
    }

    pub(crate) fn mark_promotions_committed(&mut self) {
        for health in self.health.values_mut() {
            if health.status == SourceRuntimePhase::Promoting {
                health.status = SourceRuntimePhase::Ready;
            }
        }
    }

    pub(crate) fn mark_failure(
        &mut self,
        source_id: &str,
        has_last_good: bool,
        error: Option<SourceRuntimeError>,
    ) {
        if has_last_good {
            self.known_last_good.insert(source_id.to_owned());
        }
        let consecutive_failures = {
            let health = self.health_entry(source_id);
            health.status = if has_last_good {
                SourceRuntimePhase::Degraded
            } else {
                SourceRuntimePhase::Unavailable
            };
            health.consecutive_failures = health.consecutive_failures.saturating_add(1);
            health.stale = has_last_good;
            health.last_error = error;
            health.consecutive_failures
        };
        let backoff_seconds = SourceRetryPolicy::default().backoff_seconds(consecutive_failures);
        let retry_after_unix_nanos = retry_after_unix_nanos(backoff_seconds);
        self.retry_after.insert(
            source_id.to_owned(),
            std::time::Instant::now() + std::time::Duration::from_secs(backoff_seconds),
        );
        let health = self.health_entry(source_id);
        health.retry_after_unix_nanos = Some(retry_after_unix_nanos);
        health.retry_backoff_seconds = Some(backoff_seconds);
    }

    pub(crate) fn mark_syncing(
        &mut self,
        source_id: &str,
        progress: SourceRuntimeProgress,
        work_item: SourceRuntimeWorkItem,
    ) {
        let health = self.health_entry(source_id);
        health.status = SourceRuntimePhase::Scanning;
        health.progress = progress;
        health.work_item = work_item;
        health.stale = false;
        health.retry_after_unix_nanos = None;
        health.retry_backoff_seconds = None;
        health.last_error = None;
        self.retry_after.remove(source_id);
    }

    pub(crate) fn mark_current_inactive(&mut self, source_id: &str) {
        let desired_state = self
            .definitions
            .get(source_id)
            .map(|definition| definition.desired_state)
            .filter(|desired_state| {
                matches!(
                    desired_state,
                    SourceDesiredState::Paused
                        | SourceDesiredState::Disabled
                        | SourceDesiredState::Removed
                )
            })
            .unwrap_or(SourceDesiredState::Paused);
        self.mark_inactive(source_id, desired_state);
    }

    pub(crate) fn mark_inactive(&mut self, source_id: &str, desired_state: SourceDesiredState) {
        self.inactive.insert(source_id.to_owned());
        self.set_desired_state(source_id, desired_state);
        let health = self.health_entry(source_id);
        health.status = inactive_runtime_phase(desired_state);
        if let Some(definition) = health.definition.as_mut() {
            definition.desired_state = desired_state;
        }
        health.work_item = SourceRuntimeWorkItem::default();
        health.retry_after_unix_nanos = None;
        health.retry_backoff_seconds = None;
        health.stale = false;
        health.last_error = None;
        self.retry_after.remove(source_id);
    }

    pub(crate) fn mark_resumed(&mut self, source_id: &str) {
        self.inactive.remove(source_id);
        self.set_desired_state(source_id, SourceDesiredState::Enabled);
        self.retry_after.remove(source_id);
        if let Some(health) = self.health.get_mut(source_id) {
            health.status = SourceRuntimePhase::Idle;
            if let Some(definition) = health.definition.as_mut() {
                definition.desired_state = SourceDesiredState::Enabled;
            }
            health.progress = SourceRuntimeProgress::unknown();
            health.work_item = SourceRuntimeWorkItem::default();
            health.retry_after_unix_nanos = None;
            health.retry_backoff_seconds = None;
            health.stale = false;
            health.last_error = None;
        }
    }
}
