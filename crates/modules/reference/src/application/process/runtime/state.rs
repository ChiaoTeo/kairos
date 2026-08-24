//! Process-owned runtime and tick state.

use kairos_primitives::time::UnixNanos;

use crate::domain::{ReferenceError, SourceHealth, SourceRuntimePhase, SourceRuntimeProgressKind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ReferenceApplicationPhase {
    Booting,
    Loading,
    Serving,
    Ticking,
    Scanning,
    Reconciling,
    Publishing,
    Degraded,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ReferenceTickTrigger {
    Manual,
    Rpc,
    Timer,
    Startup,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct ReferenceTickTiming {
    pub last_started_unix_nanos: Option<UnixNanos>,
    pub last_finished_unix_nanos: Option<UnixNanos>,
    pub last_duration_millis: Option<u64>,
    pub next_due_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ReferenceAppErrorSummary {
    pub code: String,
    pub retryable: bool,
    pub message: String,
}

#[derive(Clone, Debug)]
pub(crate) struct ReferenceApplicationRuntime {
    phase: ReferenceApplicationPhase,
    run_id: String,
    next_tick_sequence: u64,
    tick_timing: ReferenceTickTiming,
    last_tick_error: Option<ReferenceAppErrorSummary>,
    last_publication_error: Option<ReferenceAppErrorSummary>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct ReferenceTickLogSummary {
    pub(crate) source_count: usize,
    pub(crate) sources_scanned: usize,
    pub(crate) sources_completed: usize,
    pub(crate) sources_degraded: usize,
    pub(crate) sources_syncing: usize,
    pub(crate) sources_stale: usize,
}

impl ReferenceApplicationRuntime {
    pub(crate) fn new(actor_id: &str) -> Self {
        Self {
            phase: ReferenceApplicationPhase::Booting,
            run_id: reference_run_id(actor_id),
            next_tick_sequence: 1,
            tick_timing: ReferenceTickTiming::default(),
            last_tick_error: None,
            last_publication_error: None,
        }
    }

    pub(crate) fn phase(&self) -> ReferenceApplicationPhase {
        self.phase
    }

    pub(crate) fn set_phase(&mut self, phase: ReferenceApplicationPhase) {
        self.phase = phase;
    }

    pub(crate) fn run_id(&self) -> &str {
        self.run_id.as_str()
    }

    pub(crate) fn tick_timing(&self) -> ReferenceTickTiming {
        self.tick_timing
    }

    pub(crate) fn last_tick_error(&self) -> Option<&ReferenceAppErrorSummary> {
        self.last_tick_error.as_ref()
    }

    pub(crate) fn record_tick_error(&mut self, error: &ReferenceError) {
        self.last_tick_error = Some(ReferenceAppErrorSummary::from_error(error));
    }

    pub(crate) fn record_tick_ready(&mut self) {
        self.last_tick_error = None;
    }

    pub(crate) fn last_publication_error(&self) -> Option<&ReferenceAppErrorSummary> {
        self.last_publication_error.as_ref()
    }

    pub(crate) fn record_publication_error_summary(
        &mut self,
        code: impl Into<String>,
        retryable: bool,
        message: impl Into<String>,
    ) {
        self.last_publication_error = Some(ReferenceAppErrorSummary {
            code: code.into(),
            retryable,
            message: message.into(),
        });
    }

    pub(crate) fn record_publication_ready(&mut self) {
        self.last_publication_error = None;
    }

    pub(crate) fn next_tick_id(&mut self) -> String {
        let sequence = self.next_tick_sequence;
        self.next_tick_sequence = self.next_tick_sequence.saturating_add(1);
        format!("{}:tick:{sequence:020}", self.run_id)
    }

    pub(crate) fn record_tick_started(&mut self, started_unix_nanos: UnixNanos) {
        self.tick_timing.last_started_unix_nanos = Some(started_unix_nanos);
        self.tick_timing.last_finished_unix_nanos = None;
        self.tick_timing.last_duration_millis = None;
    }

    pub(crate) fn record_tick_finished(
        &mut self,
        started_unix_nanos: UnixNanos,
        elapsed: std::time::Duration,
        refresh_interval: std::time::Duration,
    ) {
        let finished_unix_nanos = crate::services::time::unix_nanos();
        self.tick_timing.last_started_unix_nanos = Some(started_unix_nanos);
        self.tick_timing.last_finished_unix_nanos = Some(finished_unix_nanos);
        self.tick_timing.last_duration_millis = Some(elapsed.as_millis() as u64);
        self.tick_timing.next_due_unix_nanos = Some(UnixNanos::from(
            finished_unix_nanos
                .get()
                .saturating_add(refresh_interval.as_nanos() as u64),
        ));
    }
}

impl ReferenceAppErrorSummary {
    pub(crate) fn from_error(error: &ReferenceError) -> Self {
        Self {
            code: error.code().to_owned(),
            retryable: error.retryable(),
            message: error.to_string(),
        }
    }
}

impl ReferenceTickTrigger {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Manual => "manual",
            Self::Rpc => "rpc",
            Self::Timer => "timer",
            Self::Startup => "startup",
        }
    }
}

fn reference_run_id(actor_id: &str) -> String {
    let started_unix_nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("{actor_id}:run:{started_unix_nanos}")
}

pub(crate) fn tick_log_summary(source_health: &[SourceHealth]) -> ReferenceTickLogSummary {
    let mut summary = ReferenceTickLogSummary {
        source_count: source_health.len(),
        ..ReferenceTickLogSummary::default()
    };
    for health in source_health {
        if health.last_attempt_unix_nanos.is_some() {
            summary.sources_scanned += 1;
        }
        if health.last_success_unix_nanos.is_some()
            && matches!(health.progress.kind, SourceRuntimeProgressKind::Complete)
        {
            summary.sources_completed += 1;
        }
        if !health.status.is_healthy_for_catalog() {
            summary.sources_degraded += 1;
        }
        if matches!(
            health.status,
            SourceRuntimePhase::Scanning
                | SourceRuntimePhase::Promoting
                | SourceRuntimePhase::Syncing
        ) {
            summary.sources_syncing += 1;
        }
        if health.stale {
            summary.sources_stale += 1;
        }
    }
    summary
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::SourceRuntimeProgress;

    #[test]
    fn tick_log_summary_uses_source_runtime_language() {
        let summary = tick_log_summary(&[
            source_health(
                "ready-source",
                SourceRuntimePhase::Ready,
                SourceRuntimeProgress::complete(Some(1), Some(1), Some(10), Some(2)),
                Some(10.into()),
                Some(20.into()),
                false,
            ),
            source_health(
                "scanning-source",
                SourceRuntimePhase::Scanning,
                SourceRuntimeProgress::paged(Some(1), Some(2), Some(20), Some(5)),
                Some(30.into()),
                None,
                false,
            ),
            source_health(
                "stale-source",
                SourceRuntimePhase::Degraded,
                SourceRuntimeProgress::unknown(),
                None,
                Some(40.into()),
                true,
            ),
        ]);

        assert_eq!(summary.source_count, 3);
        assert_eq!(summary.sources_scanned, 2);
        assert_eq!(summary.sources_completed, 1);
        assert_eq!(summary.sources_degraded, 1);
        assert_eq!(summary.sources_syncing, 1);
        assert_eq!(summary.sources_stale, 1);
    }

    fn source_health(
        source_id: &str,
        status: SourceRuntimePhase,
        progress: SourceRuntimeProgress,
        last_attempt_unix_nanos: Option<UnixNanos>,
        last_success_unix_nanos: Option<UnixNanos>,
        stale: bool,
    ) -> SourceHealth {
        SourceHealth {
            source_id: source_id.into(),
            definition: None,
            status,
            progress,
            work_item: Default::default(),
            last_attempt_unix_nanos,
            last_success_unix_nanos,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: None,
            consecutive_failures: 0,
            stale,
            last_error: None,
        }
    }
}
