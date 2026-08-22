use crate::domain::{ReferenceError, SourceRuntimeProgress, SourceRuntimeWorkItem};
use crate::logging::events as log_events;
use crate::services::sources::SourceUpdate;

pub(crate) fn source_runtime_progress(
    update: &SourceUpdate,
    complete: bool,
) -> SourceRuntimeProgress {
    let pages_done = update.pages_done.or(Some(update.page_count as u64));
    let records_seen = update
        .records_seen
        .or_else(|| Some(update.catalog.record_count() as u64));
    if complete {
        SourceRuntimeProgress::complete(
            pages_done,
            update.pages_total.or(pages_done),
            records_seen,
            update.records_changed,
        )
    } else {
        SourceRuntimeProgress::paged(
            pages_done,
            update.pages_total,
            records_seen,
            update.records_changed,
        )
    }
}

pub(crate) fn source_work_item(update: &SourceUpdate) -> SourceRuntimeWorkItem {
    SourceRuntimeWorkItem {
        work_item_id: update.work_item_id.clone(),
        scope_id: update.scope_id.clone(),
        scope_kind: update.scope_kind.clone(),
        cursor_present: update.cursor_present,
        skip_reason: None,
    }
}

pub(crate) fn log_source_scan_completed(
    source_id: &str,
    update: &SourceUpdate,
    has_last_known_good: Option<bool>,
) {
    let log_event = log_events::SOURCE_SCAN_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_provider_scan_completed",
        source_id,
        work_item_id = update.work_item_id.as_deref(),
        scope_id = update.scope_id.as_deref(),
        scope_kind = update.scope_kind.as_deref(),
        progress_kind = "complete",
        pages_done = update.pages_done.unwrap_or(update.page_count as u64),
        pages_total = update
            .pages_total
            .or(update.pages_done)
            .or(Some(update.page_count as u64)),
        records_seen = update
            .records_seen
            .or_else(|| Some(update.catalog.record_count() as u64)),
        records_changed = update.records_changed,
        cursor_present = update.cursor_present,
        complete = true,
        facts_persisted = update.facts_persisted,
        has_last_known_good,
        page_count = update.page_count,
        "normalized provider facts are ready for atomic promotion"
    );
}

pub(crate) fn log_source_candidate_completed(source_id: &str, update: &SourceUpdate) {
    let log_event = log_events::SOURCE_CANDIDATE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_provider_candidate_completed",
        source_id,
        work_item_id = update.work_item_id.as_deref(),
        scope_id = update.scope_id.as_deref(),
        scope_kind = update.scope_kind.as_deref(),
        records_seen = update
            .records_seen
            .or_else(|| Some(update.catalog.record_count() as u64)),
        records_changed = update.records_changed,
        facts_persisted = update.facts_persisted,
        "complete provider candidate is ready for reference reconciliation"
    );
}

pub(crate) fn log_source_scan_progress(
    source_id: &str,
    update: &SourceUpdate,
    has_last_known_good: Option<bool>,
) {
    let log_event = log_events::SOURCE_SCAN_PROGRESS;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_provider_sync_in_progress",
        source_id,
        work_item_id = update.work_item_id.as_deref(),
        scope_id = update.scope_id.as_deref(),
        scope_kind = update.scope_kind.as_deref(),
        progress_kind = "paged",
        pages_done = update.pages_done.unwrap_or(update.page_count as u64),
        pages_total = update.pages_total,
        records_seen = update
            .records_seen
            .or_else(|| Some(update.catalog.record_count() as u64)),
        records_changed = update.records_changed,
        cursor_present = update.cursor_present,
        complete = false,
        facts_persisted = update.facts_persisted,
        has_last_known_good,
        page_count = update.page_count,
        "normalized provider scan will resume from its durable cursor"
    );
}

pub(crate) fn log_source_scan_failed(
    source_id: &str,
    error: &ReferenceError,
    has_last_known_good: bool,
) {
    let (record_kind, record_id) = error.record_identity().unwrap_or(("", ""));
    let log_event = if has_last_known_good {
        log_events::SOURCE_SCAN_DEGRADED
    } else {
        log_events::SOURCE_SCAN_FAILED
    };
    tracing::warn!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = if has_last_known_good { "reference_provider_degraded" } else { "reference_provider_unavailable" },
        source_id,
        error_code = error.code(),
        retryable = error.retryable(),
        record_kind,
        record_id,
        fallback = if has_last_known_good { "last_known_good" } else { "none" },
        error = %error,
        "reference provider refresh failed"
    );
}

#[cfg(test)]
pub(crate) fn log_source_work_completed(
    source_id: &str,
    duration_ms: u64,
    success: bool,
    complete: bool,
    page_count: usize,
    legacy_event: &'static str,
) {
    let log_event = log_events::SOURCE_WORK_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event,
        source_id,
        duration_ms,
        success,
        complete,
        page_count,
        "reference provider fetch completed"
    );
}

#[cfg(test)]
pub(crate) fn log_source_scan_degraded(failures: &[String]) {
    let log_event = log_events::SOURCE_SCAN_DEGRADED;
    tracing::warn!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_provider_degraded",
        failures = ?failures,
        "reference refresh used last-known-good provider snapshots"
    );
}

#[cfg(test)]
pub(crate) fn log_source_fan_in_completed(
    provider_count: usize,
    successful_sources: usize,
    duration_ms: u64,
) {
    let log_event = log_events::SOURCE_CANDIDATE_COMPLETED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event = "reference_provider_fan_in_completed",
        provider_count,
        successful_sources,
        duration_ms,
        "reference provider fan-in completed"
    );
}
