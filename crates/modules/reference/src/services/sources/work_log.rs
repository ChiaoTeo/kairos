//! Source work item lifecycle log helpers.

use crate::domain::{SourceWorkItem, SourceWorkReason};
use crate::logging::events as log_events;
use crate::services::runtime::SourceScheduleSkipReason;

pub(crate) fn log_source_work_skipped(source_id: &str, reason: SourceScheduleSkipReason) {
    let log_event = log_events::SOURCE_WORK_SKIPPED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        source_id,
        skip_reason = reason.as_str(),
        "reference source work item skipped"
    );
}

pub(crate) fn log_source_work_item_skipped(
    work_item: &SourceWorkItem,
    reason: SourceScheduleSkipReason,
) {
    let log_event = log_events::SOURCE_WORK_SKIPPED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        source_id = %work_item.source_id,
        work_item_id = %work_item.work_item_id,
        scope_id = work_item.scope.id.as_deref(),
        scope_kind = work_item.scope.kind.as_str(),
        skip_reason = reason.as_str(),
        "reference source work item skipped"
    );
}

pub(crate) fn log_source_work_started(work_item: &SourceWorkItem) {
    let log_event = log_events::SOURCE_WORK_STARTED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        source_id = %work_item.source_id,
        work_item_id = %work_item.work_item_id,
        scope_id = work_item.scope.id.as_deref(),
        scope_kind = work_item.scope.kind.as_str(),
        work_reason = work_item.reason.as_str(),
        "reference source work item started"
    );
}

pub(crate) fn log_source_retry_scheduled(work_item: &SourceWorkItem) {
    if !matches!(work_item.reason, SourceWorkReason::Retry) {
        return;
    }
    let log_event = log_events::SCHEDULER_RETRY_SCHEDULED;
    tracing::info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        source_id = %work_item.source_id,
        work_item_id = %work_item.work_item_id,
        scope_id = work_item.scope.id.as_deref(),
        scope_kind = work_item.scope.kind.as_str(),
        "reference source retry work item scheduled"
    );
}
