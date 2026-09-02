//! Runtime startup sequencing.

use kairos_conflux::Context;

use crate::application::{ReferenceApplication, ReferenceApplicationPhase, ReferenceTickTrigger};
use crate::domain::ReferenceResult;
use crate::logging::events as log_events;

impl ReferenceApplication {
    pub(crate) async fn start_runtime(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> ReferenceResult<()> {
        self.set_app_phase(ReferenceApplicationPhase::Loading);
        log_runtime_stage_started("activate_sources");
        self.activate_sources(&mut context.connections()).await?;
        log_runtime_stage_completed("activate_sources");

        if self.initial_refresh() {
            log_runtime_stage_started("initial_refresh");
            if let Err(error) = self
                .advance_sources_with_trigger(
                    &mut context.connections(),
                    ReferenceTickTrigger::Startup,
                )
                .await
            {
                log_initial_refresh_deferred(&error);
                log_runtime_stage_degraded("initial_refresh", &error);
            } else {
                log_runtime_stage_completed("initial_refresh");
            }
        }

        log_runtime_stage_started("publish_pending");
        if let Err(error) = self.publish_pending_to_outputs(context).await {
            log_runtime_stage_degraded("publish_pending", &error);
        } else {
            log_runtime_stage_completed("publish_pending");
        }

        context.spawn_timer("refresh", self.refresh_interval());
        self.set_app_phase(ReferenceApplicationPhase::Serving);
        log_runtime_serving(self.refresh_interval().as_millis() as u64);
        Ok(())
    }
}

fn log_runtime_stage_started(stage: &'static str) {
    let log_event = log_events::APP_PHASE_STARTED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_runtime_stage_started",
        stage = stage,
        "reference runtime stage started"
    )
}

fn log_runtime_stage_completed(stage: &'static str) {
    let log_event = log_events::APP_PHASE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_runtime_stage_completed",
        stage = stage,
        "reference runtime stage completed"
    )
}

fn log_runtime_stage_degraded(stage: &'static str, error: &impl std::fmt::Debug) {
    let log_event = log_events::APP_PHASE_DEGRADED;
    tracing::warn!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_runtime_stage_degraded",
        stage = stage,
        error = ?error,
        "reference runtime stage degraded"
    )
}

fn log_initial_refresh_deferred(error: &impl std::fmt::Debug) {
    let log_event = log_events::APP_TICK_DEGRADED;
    tracing::warn!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_initial_refresh_deferred",
        error = ?error,
        "Reference starts from its durable catalog while provider synchronization retries"
    )
}

fn log_runtime_serving(refresh_interval_ms: u64) {
    let log_event = log_events::APP_PHASE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_runtime_stage_completed",
        stage = "serve",
        refresh_interval_ms = refresh_interval_ms,
        "reference runtime is serving"
    )
}
