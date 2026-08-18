use std::collections::{BTreeMap, BTreeSet, VecDeque};

use kairos_conflux::{ConnectionHealth, ConnectionLifecycle, ExternalAccountEventEnvelope};

use crate::domain::SegmentKey;

pub(crate) const RECOVERY_EVENT_CAPACITY: usize = 4_096;
pub(crate) const RETAINED_EVENT_IDS: usize = 4_096;

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SegmentSyncMode {
    SnapshotThenStream,
    SnapshotOnly,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SegmentSyncLifecycle {
    Configured,
    Bootstrapping,
    Live,
    SnapshotCurrent,
    Degraded,
    Resyncing,
    Unavailable,
    Stopped,
}

/// Process-private synchronization state. Account business facts remain owned
/// exclusively by `AccountActor`; this state only records provider delivery,
/// recovery, and freshness evidence for one configured segment.
pub(crate) struct SegmentSyncState {
    pub(crate) mode: SegmentSyncMode,
    pub(crate) lifecycle: SegmentSyncLifecycle,
    pub(crate) initial_snapshot_complete: bool,
    pub(crate) refresh_pending: bool,
    pub(crate) refresh_requested: bool,
    pub(crate) last_snapshot_at_unix_nanos: Option<u64>,
    pub(crate) last_event_at_unix_nanos: Option<u64>,
    pub(crate) last_success_at_unix_nanos: Option<u64>,
    pub(crate) last_refresh_duration_ms: Option<u64>,
    pub(crate) last_error: Option<String>,
    pub(crate) channel_health: BTreeMap<String, ConnectionHealth>,
    pub(crate) resync_required_bindings: BTreeSet<String>,
    pub(crate) event_watermarks: BTreeMap<(String, String), (u64, Option<u64>)>,
    pub(crate) event_ids: BTreeSet<(String, String, String)>,
    pub(crate) event_id_order: VecDeque<(String, String, String)>,
    pub(crate) recovery_events: VecDeque<ExternalAccountEventEnvelope>,
    pub(crate) recovery_overflowed: bool,
}

impl SegmentSyncState {
    pub(crate) fn new(_segment_key: SegmentKey) -> Self {
        Self {
            mode: SegmentSyncMode::SnapshotOnly,
            lifecycle: SegmentSyncLifecycle::Configured,
            initial_snapshot_complete: false,
            refresh_pending: false,
            refresh_requested: false,
            last_snapshot_at_unix_nanos: None,
            last_event_at_unix_nanos: None,
            last_success_at_unix_nanos: None,
            last_refresh_duration_ms: None,
            last_error: None,
            channel_health: BTreeMap::new(),
            resync_required_bindings: BTreeSet::new(),
            event_watermarks: BTreeMap::new(),
            event_ids: BTreeSet::new(),
            event_id_order: VecDeque::new(),
            recovery_events: VecDeque::new(),
            recovery_overflowed: false,
        }
    }

    pub(crate) fn add_stream(&mut self, binding_id: String) {
        self.mode = SegmentSyncMode::SnapshotThenStream;
        self.channel_health.insert(
            binding_id,
            ConnectionHealth {
                lifecycle: ConnectionLifecycle::Created,
                healthy: false,
                authenticated: false,
                last_error: None,
            },
        );
    }

    pub(crate) fn mark_stream_ready(&mut self, binding_id: &str) {
        self.channel_health.insert(
            binding_id.to_owned(),
            ConnectionHealth {
                lifecycle: ConnectionLifecycle::Ready,
                healthy: true,
                authenticated: true,
                last_error: None,
            },
        );
        self.complete_resync();
    }

    pub(crate) fn mark_stream_failed(&mut self, binding_id: &str, error: String) {
        self.channel_health.insert(
            binding_id.to_owned(),
            ConnectionHealth {
                lifecycle: ConnectionLifecycle::Failed,
                healthy: false,
                authenticated: false,
                last_error: Some(error.clone()),
            },
        );
        self.resync_required_bindings.insert(binding_id.to_owned());
        self.mark_resync(error);
    }

    pub(crate) fn begin_refresh(&mut self) {
        self.refresh_pending = true;
        self.lifecycle = if self.initial_snapshot_complete {
            SegmentSyncLifecycle::Resyncing
        } else {
            SegmentSyncLifecycle::Bootstrapping
        };
    }

    pub(crate) fn snapshot_succeeded(&mut self, observed_at: u64, elapsed_ms: u64) {
        self.refresh_pending = false;
        self.initial_snapshot_complete = true;
        self.last_snapshot_at_unix_nanos = Some(observed_at);
        self.last_success_at_unix_nanos = Some(now_unix_nanos());
        self.last_refresh_duration_ms = Some(elapsed_ms);
        self.last_error = None;
        self.event_watermarks.clear();
        self.lifecycle = match self.mode {
            SegmentSyncMode::SnapshotOnly => SegmentSyncLifecycle::SnapshotCurrent,
            SegmentSyncMode::SnapshotThenStream => SegmentSyncLifecycle::Resyncing,
        };
    }

    pub(crate) fn snapshot_failed(&mut self, error: String, elapsed_ms: u64) {
        self.refresh_pending = false;
        self.last_refresh_duration_ms = Some(elapsed_ms);
        self.last_error = Some(error);
        self.lifecycle = if self.initial_snapshot_complete {
            SegmentSyncLifecycle::Degraded
        } else {
            SegmentSyncLifecycle::Unavailable
        };
    }

    pub(crate) fn requires_buffering(&self) -> bool {
        self.mode == SegmentSyncMode::SnapshotThenStream
            && (self.refresh_pending
                || !self.initial_snapshot_complete
                || matches!(
                    self.lifecycle,
                    SegmentSyncLifecycle::Resyncing | SegmentSyncLifecycle::Degraded
                ))
    }

    pub(crate) fn buffer(&mut self, event: ExternalAccountEventEnvelope) -> bool {
        if self.recovery_events.len() >= RECOVERY_EVENT_CAPACITY {
            self.recovery_overflowed = true;
            self.lifecycle = SegmentSyncLifecycle::Resyncing;
            self.last_error = Some("segment recovery event buffer overflowed".into());
            return false;
        }
        self.recovery_events.push_back(event);
        true
    }

    pub(crate) fn mark_resync(&mut self, error: impl Into<String>) {
        self.lifecycle = SegmentSyncLifecycle::Resyncing;
        self.last_error = Some(error.into());
        self.refresh_requested = true;
    }

    pub(crate) fn complete_resync(&mut self) {
        let mut remaining = BTreeSet::new();
        for binding_id in std::mem::take(&mut self.resync_required_bindings) {
            let Some(health) = self.channel_health.get_mut(&binding_id) else {
                continue;
            };
            if health.authenticated {
                health.lifecycle = ConnectionLifecycle::Ready;
                health.healthy = true;
                health.last_error = None;
            } else {
                remaining.insert(binding_id);
            }
        }
        self.resync_required_bindings = remaining;
        if self.mode == SegmentSyncMode::SnapshotThenStream
            && self.initial_snapshot_complete
            && self
                .channel_health
                .values()
                .all(|health| health.healthy && health.authenticated)
            && self.resync_required_bindings.is_empty()
        {
            self.lifecycle = SegmentSyncLifecycle::Live;
            self.last_error = None;
            self.last_success_at_unix_nanos = Some(now_unix_nanos());
        }
    }

    pub(crate) fn mark_event_success(&mut self, observed_at: u64) {
        self.last_event_at_unix_nanos = Some(observed_at);
        self.last_success_at_unix_nanos = Some(now_unix_nanos());
        if self.initial_snapshot_complete && self.resync_required_bindings.is_empty() {
            self.lifecycle = SegmentSyncLifecycle::Live;
            self.last_error = None;
        }
    }

    pub(crate) fn ready(&self) -> bool {
        matches!(
            self.lifecycle,
            SegmentSyncLifecycle::Live | SegmentSyncLifecycle::SnapshotCurrent
        )
    }

    pub(crate) fn evaluate_freshness(
        &mut self,
        now_unix_nanos: u64,
        stale_after: std::time::Duration,
        unavailable_after: std::time::Duration,
    ) {
        let Some(last_success) = self.last_success_at_unix_nanos else {
            return;
        };
        let age = now_unix_nanos.saturating_sub(last_success);
        if age > unavailable_after.as_nanos().min(u64::MAX as u128) as u64 {
            self.lifecycle = SegmentSyncLifecycle::Unavailable;
            self.last_error.get_or_insert_with(|| {
                format!("segment has had no successful provider observation for {age}ns")
            });
        } else if age > stale_after.as_nanos().min(u64::MAX as u128) as u64
            && !matches!(self.lifecycle, SegmentSyncLifecycle::Resyncing)
        {
            self.lifecycle = SegmentSyncLifecycle::Degraded;
            self.last_error
                .get_or_insert_with(|| format!("segment provider observation is stale by {age}ns"));
        }
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn freshness_deadlines_degrade_then_make_only_that_segment_unavailable() {
        let mut state = SegmentSyncState::new(SegmentKey::new("funding").unwrap());
        state.snapshot_succeeded(10, 2);
        state.last_success_at_unix_nanos = Some(1_000);

        state.evaluate_freshness(
            3_001,
            std::time::Duration::from_nanos(2_000),
            std::time::Duration::from_nanos(6_000),
        );
        assert_eq!(state.lifecycle, SegmentSyncLifecycle::Degraded);

        state.evaluate_freshness(
            7_001,
            std::time::Duration::from_nanos(2_000),
            std::time::Duration::from_nanos(6_000),
        );
        assert_eq!(state.lifecycle, SegmentSyncLifecycle::Unavailable);
        assert!(state.last_error.is_some());
    }
}
