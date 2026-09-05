//! Runtime tick orchestration.

use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use tracing::{info, warn};

use crate::application::ReferenceApplication;
use crate::application::process::runtime::state::{
    ReferenceApplicationPhase, ReferenceTickTrigger, tick_log_summary,
};
use crate::domain::ReferenceResult;
use crate::logging::events as log_events;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceRefreshResult {
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub changed: bool,
    pub change_count: usize,
}

impl ReferenceApplication {
    fn record_tick_finished(
        &mut self,
        started_unix_nanos: UnixNanos,
        elapsed: std::time::Duration,
    ) {
        self.runtime
            .record_tick_finished(started_unix_nanos, elapsed, self.refresh_interval);
    }

    /// Advance the source workflow, reconcile lifecycle changes, and persist
    /// the resulting catalog.
    pub async fn advance_sources_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        self.refresh_inner(None, connections, ReferenceTickTrigger::Manual)
            .await
    }

    pub(crate) async fn advance_sources_with_trigger(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        trigger: ReferenceTickTrigger,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        self.refresh_inner(None, connections, trigger).await
    }

    pub(crate) async fn advance_timer_tick(
        &mut self,
        context: &mut kairos_conflux::Context<'_, Self>,
    ) -> ReferenceResult<()> {
        if let Err(error) = self
            .advance_sources_with_trigger(&mut context.connections(), ReferenceTickTrigger::Timer)
            .await
        {
            log_timer_refresh_failed(&error);
        }
        if let Err(error) = self.publish_pending_to_outputs(context).await {
            warn!(
                error = ?error,
                "Reference publication remains pending after timer tick"
            );
        }
        Ok(())
    }

    /// User-facing refresh command compatibility. Internally this advances the
    /// Reference source workflow rather than performing a one-shot full fetch.
    pub async fn refresh_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        self.advance_sources_with_connections(connections).await
    }

    #[cfg(test)]
    pub async fn refresh(&mut self) -> ReferenceResult<ReferenceRefreshResult> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        self.refresh_with_connections(&mut system.connections())
            .await
    }

    /// Advance one configured source without re-querying unrelated sources.
    /// A completed source/scope candidate is reconciled through the normal
    /// global refresh path before it becomes visible.
    pub async fn advance_source_with_connections(
        &mut self,
        source_id: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        self.refresh_inner(Some(source_id), connections, ReferenceTickTrigger::Manual)
            .await
    }

    pub(crate) async fn advance_source_with_trigger(
        &mut self,
        source_id: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        trigger: ReferenceTickTrigger,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        self.refresh_inner(Some(source_id), connections, trigger)
            .await
    }

    /// User-facing refresh command compatibility for one configured source.
    pub async fn refresh_source_with_connections(
        &mut self,
        source_id: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        self.advance_source_with_connections(source_id, connections)
            .await
    }

    async fn refresh_inner(
        &mut self,
        source_id: Option<&str>,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        trigger: ReferenceTickTrigger,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        let started = std::time::Instant::now();
        let tick_started_unix_nanos = crate::services::time::unix_nanos();
        self.runtime.record_tick_started(tick_started_unix_nanos);
        let requested_source = source_id
            .map(str::to_owned)
            .unwrap_or_else(|| self.source_id().to_owned());
        let tick_id = self.runtime.next_tick_id();
        let generation_before = self.actor.metadata.generation;
        let event_sequence_before = self.actor.metadata.event_sequence;
        self.runtime.set_phase(ReferenceApplicationPhase::Ticking);
        let log_event = log_events::APP_TICK_STARTED;
        info!(
            event = log_event.event,
            component = log_event.component,
            area = log_event.area,
            action = log_event.action,
            outcome = log_event.outcome,
            legacy_event = "reference_refresh_started",
            run_id = self.runtime.run_id(),
            tick_id = tick_id.as_str(),
            trigger = trigger.as_str(),
            source_id = requested_source.as_str(),
            generation_before = generation_before.get(),
            event_sequence_before = event_sequence_before.get(),
            "reference refresh started"
        );
        self.runtime.set_phase(ReferenceApplicationPhase::Scanning);
        let result = match source_id {
            Some(source_id) => {
                self.actor
                    .advance_source_with_connections(source_id, connections, self.tick_budget)
                    .await
            },
            None => {
                self.actor
                    .advance_sources_with_connections(connections, self.tick_budget)
                    .await
            },
        };
        let result = match result {
            Ok(result) => result,
            Err(error) => {
                self.record_tick_finished(tick_started_unix_nanos, started.elapsed());
                let source_health = self.source_health();
                let degraded_sources = source_health
                    .iter()
                    .filter(|health| !health.status.is_healthy_for_catalog())
                    .map(|health| health.source_id.as_str())
                    .collect::<Vec<_>>();
                let stale_source_count = source_health.iter().filter(|health| health.stale).count();
                if error.is_sync_in_progress() {
                    let log_event = log_events::APP_TICK_PROGRESS;
                    info!(
                        event = log_event.event,
                        component = log_event.component,
                        area = log_event.area,
                        action = log_event.action,
                        outcome = log_event.outcome,
                        legacy_event = "reference_sync_in_progress",
                        run_id = self.runtime.run_id(),
                        tick_id = tick_id.as_str(),
                        trigger = trigger.as_str(),
                        source_id = %self.source_id(),
                        duration_ms = started.elapsed().as_millis() as u64,
                        source_count = source_health.len(),
                        syncing_source_count = degraded_sources.len(),
                        syncing_sources = ?degraded_sources,
                        "reference refresh is waiting for initial source scans"
                    );
                    self.runtime.record_tick_ready();
                    self.runtime.set_phase(ReferenceApplicationPhase::Serving);
                } else {
                    self.runtime.record_tick_error(&error);
                    let log_event = log_events::APP_TICK_FAILED;
                    warn!(
                        event = log_event.event,
                        component = log_event.component,
                        area = log_event.area,
                        action = log_event.action,
                        outcome = log_event.outcome,
                        legacy_event = "reference_refresh_failed",
                        run_id = self.runtime.run_id(),
                        tick_id = tick_id.as_str(),
                        trigger = trigger.as_str(),
                        source_id = %self.source_id(),
                        duration_ms = started.elapsed().as_millis() as u64,
                        generation_before = generation_before.get(),
                        event_sequence_before = event_sequence_before.get(),
                        source_count = source_health.len(),
                        degraded_source_count = degraded_sources.len(),
                        stale_source_count,
                        degraded_sources = ?degraded_sources,
                        fallback = if stale_source_count > 0 { "last_known_good" } else { "none" },
                        error_code = error.code(),
                        retryable = error.retryable(),
                        safe_message = %error,
                        error = %error,
                        "reference refresh failed"
                    );
                    self.runtime.set_phase(ReferenceApplicationPhase::Degraded);
                }
                return Err(error);
            },
        };
        if result.event_count > 0 {
            self.runtime
                .set_phase(ReferenceApplicationPhase::Reconciling);
        }
        self.record_tick_finished(tick_started_unix_nanos, started.elapsed());
        self.runtime.record_tick_ready();
        self.runtime.set_phase(ReferenceApplicationPhase::Serving);
        let source_health = self.source_health();
        let tick_summary = tick_log_summary(&source_health);
        let pending_publications = self.pending_publication_count().await.unwrap_or(0);
        let next_tick_due_unix_nanos = self
            .runtime
            .tick_timing()
            .next_due_unix_nanos
            .map(|value| value.get());
        let log_event = log_events::APP_TICK_COMPLETED;
        info!(
            event = log_event.event,
            component = log_event.component,
            area = log_event.area,
            action = log_event.action,
            outcome = log_event.outcome,
            legacy_event = "reference_refresh_completed",
            run_id = self.runtime.run_id(),
            tick_id = tick_id.as_str(),
            trigger = trigger.as_str(),
            source_id = requested_source.as_str(),
            duration_ms = started.elapsed().as_millis() as u64,
            source_count = tick_summary.source_count,
            sources_scanned = tick_summary.sources_scanned,
            sources_completed = tick_summary.sources_completed,
            sources_degraded = tick_summary.sources_degraded,
            sources_syncing = tick_summary.sources_syncing,
            sources_stale = tick_summary.sources_stale,
            changed_records = result.event_count,
            lifecycle_events = result.event_count,
            generation_before = generation_before.get(),
            generation_after = result.generation.get(),
            event_sequence_before = event_sequence_before.get(),
            event_sequence_after = result.event_sequence.get(),
            pending_publications,
            next_tick_due_unix_nanos,
            "reference refresh completed"
        );
        Ok(ReferenceRefreshResult {
            generation: result.generation,
            event_sequence: result.event_sequence,
            changed: result.changed,
            change_count: result.event_count,
        })
    }
}

fn log_timer_refresh_failed(error: &crate::domain::ReferenceError) {
    let log_event = log_events::APP_TICK_FAILED;
    warn!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_refresh_failed",
        error = %error,
        "Reference retains its last durable catalog"
    )
}
