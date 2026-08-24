//! Runtime delivery of committed outbox events to process outputs.

use kairos_conflux::Context;
use kairos_reference_contract::ReferenceControlError;

use crate::application::ReferenceApplication;
use crate::domain::{ReferenceError, ReferenceResult};
use crate::logging::events as log_events;

const DEFAULT_PUBLICATION_BATCH_LIMIT: usize = 1_024;
const REFERENCE_OUTPUT_STREAM: &str = "reference-changes";

/// Exact revisioned wire event committed with the catalog transaction.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferencePublication {
    event_id: String,
    sequence: u64,
    payload: Vec<u8>,
}

impl ReferencePublication {
    pub(crate) fn new(event_id: String, sequence: u64, payload: Vec<u8>) -> Self {
        Self {
            event_id,
            sequence,
            payload,
        }
    }

    pub fn event_id(&self) -> &str {
        &self.event_id
    }

    pub fn sequence(&self) -> u64 {
        self.sequence
    }

    pub fn payload(&self) -> &[u8] {
        &self.payload
    }
}

impl ReferenceApplication {
    pub async fn pending_publications(
        &mut self,
        limit: usize,
    ) -> ReferenceResult<Vec<ReferencePublication>> {
        Ok(self
            .actor
            .pending_publications(limit)
            .await?
            .into_iter()
            .map(|value| ReferencePublication::new(value.event_id, value.sequence, value.payload))
            .collect())
    }

    pub(crate) async fn pending_publication_count(&mut self) -> ReferenceResult<usize> {
        self.actor.pending_event_count().await
    }

    pub async fn acknowledge_publications(&mut self, event_ids: &[String]) -> ReferenceResult<()> {
        let result = self.actor.acknowledge_publications(event_ids).await;
        match &result {
            Ok(()) => {
                self.record_publication_ready();
                log_publication_ack_completed(event_ids.len());
            },
            Err(error) => {
                self.record_publication_error_summary(
                    error.code(),
                    error.retryable(),
                    error.to_string(),
                );
                log_publication_ack_failed(event_ids.len(), error);
            },
        }
        result
    }

    pub(crate) async fn publish_pending_to_outputs(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<usize, ReferenceControlError> {
        let batch_limit = publication_batch_limit(self.tick_budget());
        let pending_before = self
            .pending_publication_count()
            .await
            .map_err(control_error)?;
        let publications = self
            .pending_publications(batch_limit)
            .await
            .map_err(control_error)?;
        if publications.is_empty() {
            self.record_publication_ready();
            return Ok(0);
        }
        self.set_app_phase(crate::application::ReferenceApplicationPhase::Publishing);
        let log_event = log_events::PUBLICATION_PUBLISH_STARTED;
        tracing::info!(
            event = log_event.event,
            component = log_event.component,
            area = log_event.area,
            action = log_event.action,
            outcome = log_event.outcome,
            legacy_event = "reference_events_publish_started",
            output_stream = REFERENCE_OUTPUT_STREAM,
            batch_limit = batch_limit,
            pending_before = pending_before,
            event_count = publications.len(),
            "reference pending publications publish started"
        );

        let event_ids = match publish_publications(context, &publications) {
            Ok(event_ids) => event_ids,
            Err(error) => {
                self.set_app_phase(crate::application::ReferenceApplicationPhase::Degraded);
                self.record_publication_error_summary(
                    error.code.clone(),
                    error.retryable,
                    error.message.clone(),
                );
                log_publication_publish_degraded(
                    batch_limit,
                    pending_before,
                    publications.len(),
                    &error,
                );
                return Err(error);
            },
        };
        if let Err(error) = self
            .acknowledge_publications(&event_ids)
            .await
            .map_err(control_error)
        {
            self.set_app_phase(crate::application::ReferenceApplicationPhase::Degraded);
            self.record_publication_error_summary(
                error.code.clone(),
                error.retryable,
                error.message.clone(),
            );
            log_publication_ack_degraded(batch_limit, pending_before, event_ids.len(), &error);
            return Err(error);
        }
        let pending_after = self
            .pending_publication_count()
            .await
            .map_err(control_error)?;
        log_publication_ack_with_batch(batch_limit, pending_before, pending_after, event_ids.len());
        log_publication_publish_completed(
            batch_limit,
            pending_before,
            pending_after,
            publications.len(),
        );
        self.set_app_phase(crate::application::ReferenceApplicationPhase::Serving);
        self.record_publication_ready();
        Ok(publications.len())
    }
}

fn publish_publications(
    context: &mut Context<'_, ReferenceApplication>,
    publications: &[ReferencePublication],
) -> Result<Vec<String>, ReferenceControlError> {
    if !context.outputs().aeron.contains(REFERENCE_OUTPUT_STREAM) {
        return Err(control_error(ReferenceError::Publication(
            "reference Aeron publisher is not configured".into(),
        )));
    }
    for publication in publications {
        context
            .outputs()
            .aeron
            .publish(REFERENCE_OUTPUT_STREAM, publication.payload())
            .map_err(|error| control_error(ReferenceError::Publication(error.to_string())))?;
    }
    Ok(publications
        .iter()
        .map(|publication| publication.event_id().to_owned())
        .collect())
}

fn publication_batch_limit(budget: crate::domain::SourceTickBudget) -> usize {
    budget
        .max_publications_per_tick
        .filter(|value| *value > 0)
        .map(|value| value as usize)
        .unwrap_or(DEFAULT_PUBLICATION_BATCH_LIMIT)
}

fn control_error(error: ReferenceError) -> ReferenceControlError {
    ReferenceControlError {
        code: error.code().into(),
        message: error.to_string(),
        retryable: error.retryable(),
        details: std::collections::BTreeMap::new(),
    }
}

fn log_publication_publish_degraded(
    batch_limit: usize,
    pending_before: usize,
    event_count: usize,
    error: &ReferenceControlError,
) {
    let log_event = log_events::PUBLICATION_PUBLISH_DEGRADED;
    tracing::warn!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_events_publish_failed",
        output_stream = REFERENCE_OUTPUT_STREAM,
        batch_limit = batch_limit,
        pending_before = pending_before,
        event_count = event_count,
        error_code = error.code.as_str(),
        retryable = error.retryable,
        safe_message = error.message.as_str(),
        error = ?error,
        "reference pending publications publish degraded"
    )
}

fn log_publication_ack_degraded(
    batch_limit: usize,
    pending_before: usize,
    event_count: usize,
    error: &ReferenceControlError,
) {
    let log_event = log_events::PUBLICATION_ACK_FAILED;
    tracing::warn!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_events_ack_failed",
        output_stream = REFERENCE_OUTPUT_STREAM,
        batch_limit = batch_limit,
        pending_before = pending_before,
        event_count = event_count,
        error_code = error.code.as_str(),
        retryable = error.retryable,
        safe_message = error.message.as_str(),
        error = ?error,
        "reference pending publications ack failed"
    )
}

fn log_publication_ack_with_batch(
    batch_limit: usize,
    pending_before: usize,
    pending_after: usize,
    event_count: usize,
) {
    let log_event = log_events::PUBLICATION_ACK_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_events_acknowledged",
        output_stream = REFERENCE_OUTPUT_STREAM,
        batch_limit = batch_limit,
        pending_before = pending_before,
        pending_after = pending_after,
        event_count = event_count,
        "reference pending publications ack completed"
    )
}

fn log_publication_publish_completed(
    batch_limit: usize,
    pending_before: usize,
    pending_after: usize,
    event_count: usize,
) {
    let log_event = log_events::PUBLICATION_PUBLISH_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_events_published",
        output_stream = REFERENCE_OUTPUT_STREAM,
        batch_limit = batch_limit,
        pending_before = pending_before,
        pending_after = pending_after,
        event_count = event_count,
        "reference pending publications publish completed"
    )
}

fn log_publication_ack_completed(event_count: usize) {
    let log_event = log_events::PUBLICATION_ACK_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_events_acknowledged",
        event_count,
        "reference published events acknowledged"
    )
}

fn log_publication_ack_failed(event_count: usize, error: &ReferenceError) {
    let log_event = log_events::PUBLICATION_ACK_FAILED;
    tracing::warn!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_events_acknowledge_failed",
        event_count,
        error_code = error.code(),
        retryable = error.retryable(),
        safe_message = %error,
        error = %error,
        "reference published events acknowledgement failed"
    )
}
