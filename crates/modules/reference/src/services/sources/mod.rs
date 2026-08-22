//! Internal source workflow capability driven by the Reference actor.

mod configured;
mod model;
mod participant;
mod progress;
mod work_log;
mod workflow;

pub(crate) use configured::{ConfiguredProviderSource, ConfiguredReferenceSource};
pub(crate) use model::SourceUpdate;
pub(crate) use participant::ParticipantAugmentedSource;
pub(crate) use progress::{
    log_source_candidate_completed, log_source_scan_completed, log_source_scan_failed,
    log_source_scan_progress, source_runtime_progress, source_work_item,
};
#[cfg(test)]
pub(crate) use progress::{
    log_source_fan_in_completed, log_source_scan_degraded, log_source_work_completed,
};
pub(crate) use work_log::{
    log_source_retry_scheduled, log_source_work_item_skipped, log_source_work_skipped,
    log_source_work_started,
};
pub(crate) use workflow::ReferenceSource;
