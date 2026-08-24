//! Structured logging vocabulary for Reference.

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[doc(hidden)]
pub struct ReferenceLogEvent {
    pub component: &'static str,
    pub area: &'static str,
    pub action: &'static str,
    pub outcome: &'static str,
    pub event: &'static str,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[doc(hidden)]
pub struct ReferenceLogField {
    pub name: &'static str,
    pub category: ReferenceLogFieldCategory,
    pub legacy: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[doc(hidden)]
pub enum ReferenceLogFieldCategory {
    Identity,
    Correlation,
    Business,
    Progress,
    Diagnostic,
    Legacy,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReferenceLogArea {
    App,
    Source,
    Scheduler,
    Reconcile,
    Publication,
    Rpc,
    Startup,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReferenceLogAction {
    Phase,
    Tick,
    Command,
    Work,
    Scan,
    Candidate,
    Retry,
    Apply,
    Publish,
    Ack,
    Call,
    Stage,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReferenceLogOutcome {
    Started,
    Progress,
    Scheduled,
    Skipped,
    Completed,
    Degraded,
    Failed,
}

impl ReferenceLogArea {
    const fn as_str(self) -> &'static str {
        match self {
            Self::App => "app",
            Self::Source => "source",
            Self::Scheduler => "scheduler",
            Self::Reconcile => "reconcile",
            Self::Publication => "publication",
            Self::Rpc => "rpc",
            Self::Startup => "startup",
        }
    }
}

impl ReferenceLogAction {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Phase => "phase",
            Self::Tick => "tick",
            Self::Command => "command",
            Self::Work => "work",
            Self::Scan => "scan",
            Self::Candidate => "candidate",
            Self::Retry => "retry",
            Self::Apply => "apply",
            Self::Publish => "publish",
            Self::Ack => "ack",
            Self::Call => "call",
            Self::Stage => "stage",
        }
    }
}

impl ReferenceLogOutcome {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Started => "started",
            Self::Progress => "progress",
            Self::Scheduled => "scheduled",
            Self::Skipped => "skipped",
            Self::Completed => "completed",
            Self::Degraded => "degraded",
            Self::Failed => "failed",
        }
    }
}

impl ReferenceLogEvent {
    const fn from_dictionary(
        area: ReferenceLogArea,
        action: ReferenceLogAction,
        outcome: ReferenceLogOutcome,
        event: &'static str,
    ) -> Self {
        Self {
            component: "reference",
            area: area.as_str(),
            action: action.as_str(),
            outcome: outcome.as_str(),
            event,
        }
    }

    #[cfg(test)]
    pub(crate) fn validate(self) {
        assert_eq!(
            self.event,
            format!("{}.{}.{}", self.area, self.action, self.outcome)
        );
        assert!(!self.event.contains("reference"));
        assert!(!self.event.contains('_'));
        assert_ne!(self.area, self.action);
        assert_ne!(self.area, self.outcome);
        assert_ne!(self.action, self.outcome);
        let segments: Vec<_> = self.event.split('.').collect();
        assert_eq!(segments, [self.area, self.action, self.outcome]);
        assert_eq!(segments.len(), 3);
        segments.iter().for_each(|segment| {
            assert!(segment.chars().all(|ch| ch.is_ascii_lowercase()));
            assert_ne!(*segment, self.component);
            assert_ne!(*segment, "massive");
            assert_ne!(*segment, "options");
            assert_ne!(*segment, "equity");
            assert_ne!(*segment, "provider");
        });
    }
}

impl ReferenceLogField {
    const fn new(name: &'static str, category: ReferenceLogFieldCategory) -> Self {
        Self {
            name,
            category,
            legacy: false,
        }
    }

    const fn legacy(name: &'static str) -> Self {
        Self {
            name,
            category: ReferenceLogFieldCategory::Legacy,
            legacy: true,
        }
    }

    #[cfg(test)]
    pub(crate) fn validate(self) {
        assert!(!self.name.is_empty());
        assert!(
            self.name
                .chars()
                .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '_')
        );
        assert!(!self.name.contains("__"));
        if self.legacy {
            assert_eq!(self.category, ReferenceLogFieldCategory::Legacy);
        } else {
            assert_ne!(self.category, ReferenceLogFieldCategory::Legacy);
        }
    }
}

macro_rules! reference_log_event {
    ($area_variant:ident, $area:literal, $action_variant:ident, $action:literal, $outcome_variant:ident, $outcome:literal) => {
        ReferenceLogEvent::from_dictionary(
            super::ReferenceLogArea::$area_variant,
            super::ReferenceLogAction::$action_variant,
            super::ReferenceLogOutcome::$outcome_variant,
            concat!($area, ".", $action, ".", $outcome),
        )
    };
}

pub mod events {
    use super::ReferenceLogEvent;

    pub const APP_PHASE_STARTED: ReferenceLogEvent =
        reference_log_event!(App, "app", Phase, "phase", Started, "started");
    pub const APP_PHASE_COMPLETED: ReferenceLogEvent =
        reference_log_event!(App, "app", Phase, "phase", Completed, "completed");
    pub const APP_PHASE_DEGRADED: ReferenceLogEvent =
        reference_log_event!(App, "app", Phase, "phase", Degraded, "degraded");
    pub const APP_TICK_STARTED: ReferenceLogEvent =
        reference_log_event!(App, "app", Tick, "tick", Started, "started");
    pub const APP_TICK_PROGRESS: ReferenceLogEvent =
        reference_log_event!(App, "app", Tick, "tick", Progress, "progress");
    pub const APP_TICK_COMPLETED: ReferenceLogEvent =
        reference_log_event!(App, "app", Tick, "tick", Completed, "completed");
    pub const APP_TICK_DEGRADED: ReferenceLogEvent =
        reference_log_event!(App, "app", Tick, "tick", Degraded, "degraded");
    pub const APP_TICK_FAILED: ReferenceLogEvent =
        reference_log_event!(App, "app", Tick, "tick", Failed, "failed");
    pub const APP_COMMAND_STARTED: ReferenceLogEvent =
        reference_log_event!(App, "app", Command, "command", Started, "started");
    pub const APP_COMMAND_COMPLETED: ReferenceLogEvent =
        reference_log_event!(App, "app", Command, "command", Completed, "completed");

    pub const STARTUP_STAGE_STARTED: ReferenceLogEvent =
        reference_log_event!(Startup, "startup", Stage, "stage", Started, "started");
    pub const STARTUP_STAGE_COMPLETED: ReferenceLogEvent =
        reference_log_event!(Startup, "startup", Stage, "stage", Completed, "completed");
    pub const STARTUP_STAGE_DEGRADED: ReferenceLogEvent =
        reference_log_event!(Startup, "startup", Stage, "stage", Degraded, "degraded");
    pub const STARTUP_STAGE_FAILED: ReferenceLogEvent =
        reference_log_event!(Startup, "startup", Stage, "stage", Failed, "failed");

    pub const SOURCE_WORK_STARTED: ReferenceLogEvent =
        reference_log_event!(Source, "source", Work, "work", Started, "started");
    pub const SOURCE_WORK_SKIPPED: ReferenceLogEvent =
        reference_log_event!(Source, "source", Work, "work", Skipped, "skipped");
    pub const SOURCE_WORK_COMPLETED: ReferenceLogEvent =
        reference_log_event!(Source, "source", Work, "work", Completed, "completed");
    pub const SOURCE_WORK_DEGRADED: ReferenceLogEvent =
        reference_log_event!(Source, "source", Work, "work", Degraded, "degraded");
    pub const SOURCE_SCAN_PROGRESS: ReferenceLogEvent =
        reference_log_event!(Source, "source", Scan, "scan", Progress, "progress");
    pub const SOURCE_SCAN_COMPLETED: ReferenceLogEvent =
        reference_log_event!(Source, "source", Scan, "scan", Completed, "completed");
    pub const SOURCE_SCAN_DEGRADED: ReferenceLogEvent =
        reference_log_event!(Source, "source", Scan, "scan", Degraded, "degraded");
    pub const SOURCE_SCAN_FAILED: ReferenceLogEvent =
        reference_log_event!(Source, "source", Scan, "scan", Failed, "failed");
    pub const SOURCE_CANDIDATE_COMPLETED: ReferenceLogEvent = reference_log_event!(
        Source,
        "source",
        Candidate,
        "candidate",
        Completed,
        "completed"
    );

    pub const SCHEDULER_RETRY_SCHEDULED: ReferenceLogEvent = reference_log_event!(
        Scheduler,
        "scheduler",
        Retry,
        "retry",
        Scheduled,
        "scheduled"
    );

    pub const RECONCILE_APPLY_PROGRESS: ReferenceLogEvent =
        reference_log_event!(Reconcile, "reconcile", Apply, "apply", Progress, "progress");
    pub const RECONCILE_APPLY_COMPLETED: ReferenceLogEvent = reference_log_event!(
        Reconcile,
        "reconcile",
        Apply,
        "apply",
        Completed,
        "completed"
    );

    pub const PUBLICATION_PUBLISH_STARTED: ReferenceLogEvent = reference_log_event!(
        Publication,
        "publication",
        Publish,
        "publish",
        Started,
        "started"
    );
    pub const PUBLICATION_PUBLISH_COMPLETED: ReferenceLogEvent = reference_log_event!(
        Publication,
        "publication",
        Publish,
        "publish",
        Completed,
        "completed"
    );
    pub const PUBLICATION_PUBLISH_DEGRADED: ReferenceLogEvent = reference_log_event!(
        Publication,
        "publication",
        Publish,
        "publish",
        Degraded,
        "degraded"
    );
    pub const PUBLICATION_ACK_COMPLETED: ReferenceLogEvent = reference_log_event!(
        Publication,
        "publication",
        Ack,
        "ack",
        Completed,
        "completed"
    );
    pub const PUBLICATION_ACK_FAILED: ReferenceLogEvent =
        reference_log_event!(Publication, "publication", Ack, "ack", Failed, "failed");

    pub const RPC_CALL_STARTED: ReferenceLogEvent =
        reference_log_event!(Rpc, "rpc", Call, "call", Started, "started");
    pub const RPC_CALL_COMPLETED: ReferenceLogEvent =
        reference_log_event!(Rpc, "rpc", Call, "call", Completed, "completed");
    pub const RPC_CALL_FAILED: ReferenceLogEvent =
        reference_log_event!(Rpc, "rpc", Call, "call", Failed, "failed");

    pub const ALL: &[ReferenceLogEvent] = &[
        APP_PHASE_STARTED,
        APP_PHASE_COMPLETED,
        APP_PHASE_DEGRADED,
        APP_TICK_STARTED,
        APP_TICK_PROGRESS,
        APP_TICK_COMPLETED,
        APP_TICK_DEGRADED,
        APP_TICK_FAILED,
        APP_COMMAND_STARTED,
        APP_COMMAND_COMPLETED,
        STARTUP_STAGE_STARTED,
        STARTUP_STAGE_COMPLETED,
        STARTUP_STAGE_DEGRADED,
        STARTUP_STAGE_FAILED,
        SOURCE_WORK_STARTED,
        SOURCE_WORK_SKIPPED,
        SOURCE_WORK_COMPLETED,
        SOURCE_WORK_DEGRADED,
        SOURCE_SCAN_PROGRESS,
        SOURCE_SCAN_COMPLETED,
        SOURCE_SCAN_DEGRADED,
        SOURCE_SCAN_FAILED,
        SOURCE_CANDIDATE_COMPLETED,
        SCHEDULER_RETRY_SCHEDULED,
        RECONCILE_APPLY_PROGRESS,
        RECONCILE_APPLY_COMPLETED,
        PUBLICATION_PUBLISH_STARTED,
        PUBLICATION_PUBLISH_COMPLETED,
        PUBLICATION_PUBLISH_DEGRADED,
        PUBLICATION_ACK_COMPLETED,
        PUBLICATION_ACK_FAILED,
        RPC_CALL_STARTED,
        RPC_CALL_COMPLETED,
        RPC_CALL_FAILED,
    ];
}

pub mod fields {
    use super::{ReferenceLogField, ReferenceLogFieldCategory};

    pub const COMPONENT: ReferenceLogField =
        ReferenceLogField::new("component", ReferenceLogFieldCategory::Identity);
    pub const AREA: ReferenceLogField =
        ReferenceLogField::new("area", ReferenceLogFieldCategory::Identity);
    pub const ACTION: ReferenceLogField =
        ReferenceLogField::new("action", ReferenceLogFieldCategory::Identity);
    pub const OUTCOME: ReferenceLogField =
        ReferenceLogField::new("outcome", ReferenceLogFieldCategory::Identity);
    pub const EVENT: ReferenceLogField =
        ReferenceLogField::new("event", ReferenceLogFieldCategory::Identity);

    pub const RUN_ID: ReferenceLogField =
        ReferenceLogField::new("run_id", ReferenceLogFieldCategory::Correlation);
    pub const TICK_ID: ReferenceLogField =
        ReferenceLogField::new("tick_id", ReferenceLogFieldCategory::Correlation);
    pub const REFRESH_ID: ReferenceLogField =
        ReferenceLogField::new("refresh_id", ReferenceLogFieldCategory::Correlation);
    pub const WORK_ITEM_ID: ReferenceLogField =
        ReferenceLogField::new("work_item_id", ReferenceLogFieldCategory::Correlation);
    pub const REQUEST_ID: ReferenceLogField =
        ReferenceLogField::new("request_id", ReferenceLogFieldCategory::Correlation);
    pub const METHOD: ReferenceLogField =
        ReferenceLogField::new("method", ReferenceLogFieldCategory::Correlation);

    pub const SOURCE_ID: ReferenceLogField =
        ReferenceLogField::new("source_id", ReferenceLogFieldCategory::Business);
    pub const PROVIDER_ID: ReferenceLogField =
        ReferenceLogField::new("provider_id", ReferenceLogFieldCategory::Business);
    pub const SCOPE_ID: ReferenceLogField =
        ReferenceLogField::new("scope_id", ReferenceLogFieldCategory::Business);
    pub const SCOPE_KIND: ReferenceLogField =
        ReferenceLogField::new("scope_kind", ReferenceLogFieldCategory::Business);
    pub const GENERATION_BEFORE: ReferenceLogField =
        ReferenceLogField::new("generation_before", ReferenceLogFieldCategory::Business);
    pub const GENERATION_AFTER: ReferenceLogField =
        ReferenceLogField::new("generation_after", ReferenceLogFieldCategory::Business);
    pub const EVENT_SEQUENCE_BEFORE: ReferenceLogField =
        ReferenceLogField::new("event_sequence_before", ReferenceLogFieldCategory::Business);
    pub const EVENT_SEQUENCE_AFTER: ReferenceLogField =
        ReferenceLogField::new("event_sequence_after", ReferenceLogFieldCategory::Business);
    pub const AFFECTED_WRITE_MODE: ReferenceLogField =
        ReferenceLogField::new("affected_write_mode", ReferenceLogFieldCategory::Business);
    pub const DATABASE: ReferenceLogField =
        ReferenceLogField::new("database", ReferenceLogFieldCategory::Business);
    pub const OUTPUT_STREAM: ReferenceLogField =
        ReferenceLogField::new("output_stream", ReferenceLogFieldCategory::Business);
    pub const STAGE: ReferenceLogField =
        ReferenceLogField::new("stage", ReferenceLogFieldCategory::Business);
    pub const TRIGGER: ReferenceLogField =
        ReferenceLogField::new("trigger", ReferenceLogFieldCategory::Business);
    pub const GENERATION: ReferenceLogField =
        ReferenceLogField::new("generation", ReferenceLogFieldCategory::Business);
    pub const EVENT_SEQUENCE: ReferenceLogField =
        ReferenceLogField::new("event_sequence", ReferenceLogFieldCategory::Business);
    pub const SCAN_FORMAT_VERSION: ReferenceLogField =
        ReferenceLogField::new("scan_format_version", ReferenceLogFieldCategory::Business);

    pub const PHASE: ReferenceLogField =
        ReferenceLogField::new("phase", ReferenceLogFieldCategory::Progress);
    pub const PROGRESS_KIND: ReferenceLogField =
        ReferenceLogField::new("progress_kind", ReferenceLogFieldCategory::Progress);
    pub const PAGES_DONE: ReferenceLogField =
        ReferenceLogField::new("pages_done", ReferenceLogFieldCategory::Progress);
    pub const PAGES_TOTAL: ReferenceLogField =
        ReferenceLogField::new("pages_total", ReferenceLogFieldCategory::Progress);
    pub const RECORDS_SEEN: ReferenceLogField =
        ReferenceLogField::new("records_seen", ReferenceLogFieldCategory::Progress);
    pub const RECORDS_CHANGED: ReferenceLogField =
        ReferenceLogField::new("records_changed", ReferenceLogFieldCategory::Progress);
    pub const DURATION_MS: ReferenceLogField =
        ReferenceLogField::new("duration_ms", ReferenceLogFieldCategory::Progress);
    pub const COMPLETE: ReferenceLogField =
        ReferenceLogField::new("complete", ReferenceLogFieldCategory::Progress);
    pub const ACTIVE_MARKET_COUNT: ReferenceLogField =
        ReferenceLogField::new("active_market_count", ReferenceLogFieldCategory::Progress);
    pub const AFFECTED_ASSET_COUNT: ReferenceLogField =
        ReferenceLogField::new("affected_asset_count", ReferenceLogFieldCategory::Progress);
    pub const AFFECTED_EXCHANGE_COUNT: ReferenceLogField = ReferenceLogField::new(
        "affected_exchange_count",
        ReferenceLogFieldCategory::Progress,
    );
    pub const AFFECTED_INSTRUMENT_COUNT: ReferenceLogField = ReferenceLogField::new(
        "affected_instrument_count",
        ReferenceLogFieldCategory::Progress,
    );
    pub const AFFECTED_LISTING_COUNT: ReferenceLogField = ReferenceLogField::new(
        "affected_listing_count",
        ReferenceLogFieldCategory::Progress,
    );
    pub const AFFECTED_MARKET_COUNT: ReferenceLogField =
        ReferenceLogField::new("affected_market_count", ReferenceLogFieldCategory::Progress);
    pub const AFFECTED_TOTAL_COUNT: ReferenceLogField =
        ReferenceLogField::new("affected_total_count", ReferenceLogFieldCategory::Progress);
    pub const ASSET_COUNT: ReferenceLogField =
        ReferenceLogField::new("asset_count", ReferenceLogFieldCategory::Progress);
    pub const BATCH_LIMIT: ReferenceLogField =
        ReferenceLogField::new("batch_limit", ReferenceLogFieldCategory::Progress);
    pub const CHANGED_RECORDS: ReferenceLogField =
        ReferenceLogField::new("changed_records", ReferenceLogFieldCategory::Progress);
    pub const COMMITTED_AT_UNIX_NANOS: ReferenceLogField = ReferenceLogField::new(
        "committed_at_unix_nanos",
        ReferenceLogFieldCategory::Progress,
    );
    pub const DEGRADED_SOURCE_COUNT: ReferenceLogField =
        ReferenceLogField::new("degraded_source_count", ReferenceLogFieldCategory::Progress);
    pub const DISCARDED_INPUTS: ReferenceLogField =
        ReferenceLogField::new("discarded_inputs", ReferenceLogFieldCategory::Progress);
    pub const EXCHANGE_COUNT: ReferenceLogField =
        ReferenceLogField::new("exchange_count", ReferenceLogFieldCategory::Progress);
    pub const EVENT_COUNT: ReferenceLogField =
        ReferenceLogField::new("event_count", ReferenceLogFieldCategory::Progress);
    pub const FACTS_PERSISTED: ReferenceLogField =
        ReferenceLogField::new("facts_persisted", ReferenceLogFieldCategory::Progress);
    pub const INSTRUMENT_COUNT: ReferenceLogField =
        ReferenceLogField::new("instrument_count", ReferenceLogFieldCategory::Progress);
    pub const LIFECYCLE_EVENT_COUNT: ReferenceLogField =
        ReferenceLogField::new("lifecycle_event_count", ReferenceLogFieldCategory::Progress);
    pub const LIFECYCLE_EVENTS: ReferenceLogField =
        ReferenceLogField::new("lifecycle_events", ReferenceLogFieldCategory::Progress);
    pub const LISTING_COUNT: ReferenceLogField =
        ReferenceLogField::new("listing_count", ReferenceLogFieldCategory::Progress);
    pub const MARKET_COUNT: ReferenceLogField =
        ReferenceLogField::new("market_count", ReferenceLogFieldCategory::Progress);
    pub const PENDING_AFTER: ReferenceLogField =
        ReferenceLogField::new("pending_after", ReferenceLogFieldCategory::Progress);
    pub const PENDING_BEFORE: ReferenceLogField =
        ReferenceLogField::new("pending_before", ReferenceLogFieldCategory::Progress);
    pub const REFRESH_INTERVAL_MS: ReferenceLogField =
        ReferenceLogField::new("refresh_interval_ms", ReferenceLogFieldCategory::Progress);
    pub const RESET_PROVIDER_COUNT: ReferenceLogField =
        ReferenceLogField::new("reset_provider_count", ReferenceLogFieldCategory::Progress);
    pub const SOURCE_COUNT: ReferenceLogField =
        ReferenceLogField::new("source_count", ReferenceLogFieldCategory::Progress);
    pub const SOURCES_COMPLETED: ReferenceLogField =
        ReferenceLogField::new("sources_completed", ReferenceLogFieldCategory::Progress);
    pub const SOURCES_DEGRADED: ReferenceLogField =
        ReferenceLogField::new("sources_degraded", ReferenceLogFieldCategory::Progress);
    pub const SOURCES_SCANNED: ReferenceLogField =
        ReferenceLogField::new("sources_scanned", ReferenceLogFieldCategory::Progress);
    pub const SOURCES_STALE: ReferenceLogField =
        ReferenceLogField::new("sources_stale", ReferenceLogFieldCategory::Progress);
    pub const SOURCES_SYNCING: ReferenceLogField =
        ReferenceLogField::new("sources_syncing", ReferenceLogFieldCategory::Progress);
    pub const SYNCING_SOURCE_COUNT: ReferenceLogField =
        ReferenceLogField::new("syncing_source_count", ReferenceLogFieldCategory::Progress);

    pub const ERROR_KIND: ReferenceLogField =
        ReferenceLogField::new("error_kind", ReferenceLogFieldCategory::Diagnostic);
    pub const ERROR_CODE: ReferenceLogField =
        ReferenceLogField::new("error_code", ReferenceLogFieldCategory::Diagnostic);
    pub const ERROR: ReferenceLogField =
        ReferenceLogField::new("error", ReferenceLogFieldCategory::Diagnostic);
    pub const RETRY_AFTER_MS: ReferenceLogField =
        ReferenceLogField::new("retry_after_ms", ReferenceLogFieldCategory::Diagnostic);
    pub const RETRY_AFTER_UNIX_NANOS: ReferenceLogField = ReferenceLogField::new(
        "retry_after_unix_nanos",
        ReferenceLogFieldCategory::Diagnostic,
    );
    pub const RETRY_BACKOFF_SECONDS: ReferenceLogField = ReferenceLogField::new(
        "retry_backoff_seconds",
        ReferenceLogFieldCategory::Diagnostic,
    );
    pub const DEGRADED_REASON: ReferenceLogField =
        ReferenceLogField::new("degraded_reason", ReferenceLogFieldCategory::Diagnostic);
    pub const SAFE_MESSAGE: ReferenceLogField =
        ReferenceLogField::new("safe_message", ReferenceLogFieldCategory::Diagnostic);
    pub const HAS_LAST_KNOWN_GOOD: ReferenceLogField =
        ReferenceLogField::new("has_last_known_good", ReferenceLogFieldCategory::Diagnostic);
    pub const CURSOR_PRESENT: ReferenceLogField =
        ReferenceLogField::new("cursor_present", ReferenceLogFieldCategory::Diagnostic);
    pub const DEGRADED_SOURCES: ReferenceLogField =
        ReferenceLogField::new("degraded_sources", ReferenceLogFieldCategory::Diagnostic);
    pub const FAILURES: ReferenceLogField =
        ReferenceLogField::new("failures", ReferenceLogFieldCategory::Diagnostic);
    pub const FALLBACK: ReferenceLogField =
        ReferenceLogField::new("fallback", ReferenceLogFieldCategory::Diagnostic);
    pub const MISSING_CURRENT_EQUITY_MARKET_COUNT: ReferenceLogField = ReferenceLogField::new(
        "missing_current_equity_market_count",
        ReferenceLogFieldCategory::Diagnostic,
    );
    pub const MISSING_EQUITY_MARKET_COUNT: ReferenceLogField = ReferenceLogField::new(
        "missing_equity_market_count",
        ReferenceLogFieldCategory::Diagnostic,
    );
    pub const MISSING_PROVIDER_EQUITY_MARKET_COUNT: ReferenceLogField = ReferenceLogField::new(
        "missing_provider_equity_market_count",
        ReferenceLogFieldCategory::Diagnostic,
    );
    pub const LEGACY_EXCHANGE_LISTING_ID_COUNT: ReferenceLogField = ReferenceLogField::new(
        "legacy_exchange_listing_id_count",
        ReferenceLogFieldCategory::Diagnostic,
    );
    pub const LEGACY_EXCHANGE_MARKET_ID_COUNT: ReferenceLogField = ReferenceLogField::new(
        "legacy_exchange_market_id_count",
        ReferenceLogFieldCategory::Diagnostic,
    );
    pub const OPTION_LISTING_COUNT: ReferenceLogField = ReferenceLogField::new(
        "option_listing_count",
        ReferenceLogFieldCategory::Diagnostic,
    );
    pub const OPTION_MARKET_COUNT: ReferenceLogField =
        ReferenceLogField::new("option_market_count", ReferenceLogFieldCategory::Diagnostic);
    pub const RECORD_KIND: ReferenceLogField =
        ReferenceLogField::new("record_kind", ReferenceLogFieldCategory::Diagnostic);
    pub const RECORD_ID: ReferenceLogField =
        ReferenceLogField::new("record_id", ReferenceLogFieldCategory::Diagnostic);
    pub const RESET_PROVIDERS: ReferenceLogField =
        ReferenceLogField::new("reset_providers", ReferenceLogFieldCategory::Diagnostic);
    pub const RETRYABLE: ReferenceLogField =
        ReferenceLogField::new("retryable", ReferenceLogFieldCategory::Diagnostic);
    pub const SKIP_REASON: ReferenceLogField =
        ReferenceLogField::new("skip_reason", ReferenceLogFieldCategory::Diagnostic);
    pub const SYNCING_SOURCES: ReferenceLogField =
        ReferenceLogField::new("syncing_sources", ReferenceLogFieldCategory::Diagnostic);
    pub const WORK_REASON: ReferenceLogField =
        ReferenceLogField::new("work_reason", ReferenceLogFieldCategory::Diagnostic);

    pub const LEGACY_EVENT: ReferenceLogField = ReferenceLogField::legacy("legacy_event");
    pub const PAGE_COUNT: ReferenceLogField = ReferenceLogField::legacy("page_count");

    pub const ALL: &[ReferenceLogField] = &[
        COMPONENT,
        AREA,
        ACTION,
        OUTCOME,
        EVENT,
        RUN_ID,
        TICK_ID,
        REFRESH_ID,
        WORK_ITEM_ID,
        REQUEST_ID,
        METHOD,
        SOURCE_ID,
        PROVIDER_ID,
        SCOPE_ID,
        SCOPE_KIND,
        GENERATION_BEFORE,
        GENERATION_AFTER,
        EVENT_SEQUENCE_BEFORE,
        EVENT_SEQUENCE_AFTER,
        AFFECTED_WRITE_MODE,
        DATABASE,
        OUTPUT_STREAM,
        STAGE,
        TRIGGER,
        GENERATION,
        EVENT_SEQUENCE,
        SCAN_FORMAT_VERSION,
        PHASE,
        PROGRESS_KIND,
        PAGES_DONE,
        PAGES_TOTAL,
        RECORDS_SEEN,
        RECORDS_CHANGED,
        DURATION_MS,
        COMPLETE,
        ACTIVE_MARKET_COUNT,
        AFFECTED_ASSET_COUNT,
        AFFECTED_EXCHANGE_COUNT,
        AFFECTED_INSTRUMENT_COUNT,
        AFFECTED_LISTING_COUNT,
        AFFECTED_MARKET_COUNT,
        AFFECTED_TOTAL_COUNT,
        ASSET_COUNT,
        BATCH_LIMIT,
        CHANGED_RECORDS,
        COMMITTED_AT_UNIX_NANOS,
        DEGRADED_SOURCE_COUNT,
        DISCARDED_INPUTS,
        EXCHANGE_COUNT,
        EVENT_COUNT,
        FACTS_PERSISTED,
        INSTRUMENT_COUNT,
        LIFECYCLE_EVENT_COUNT,
        LIFECYCLE_EVENTS,
        LISTING_COUNT,
        MARKET_COUNT,
        PENDING_AFTER,
        PENDING_BEFORE,
        REFRESH_INTERVAL_MS,
        RESET_PROVIDER_COUNT,
        SOURCE_COUNT,
        SOURCES_COMPLETED,
        SOURCES_DEGRADED,
        SOURCES_SCANNED,
        SOURCES_STALE,
        SOURCES_SYNCING,
        SYNCING_SOURCE_COUNT,
        ERROR_KIND,
        ERROR_CODE,
        ERROR,
        RETRY_AFTER_MS,
        RETRY_AFTER_UNIX_NANOS,
        RETRY_BACKOFF_SECONDS,
        DEGRADED_REASON,
        SAFE_MESSAGE,
        HAS_LAST_KNOWN_GOOD,
        CURSOR_PRESENT,
        DEGRADED_SOURCES,
        FAILURES,
        FALLBACK,
        MISSING_CURRENT_EQUITY_MARKET_COUNT,
        MISSING_EQUITY_MARKET_COUNT,
        MISSING_PROVIDER_EQUITY_MARKET_COUNT,
        LEGACY_EXCHANGE_LISTING_ID_COUNT,
        LEGACY_EXCHANGE_MARKET_ID_COUNT,
        OPTION_LISTING_COUNT,
        OPTION_MARKET_COUNT,
        RECORD_KIND,
        RECORD_ID,
        RESET_PROVIDERS,
        RETRYABLE,
        SKIP_REASON,
        SYNCING_SOURCES,
        WORK_REASON,
        LEGACY_EVENT,
        PAGE_COUNT,
    ];
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::{events, fields};

    #[test]
    fn reference_log_events_use_layered_event_names() {
        events::ALL.iter().for_each(|event| event.validate());
    }

    #[test]
    fn reference_log_event_dictionary_contains_required_runtime_events() {
        let event_names = events::ALL
            .iter()
            .map(|event| event.event)
            .collect::<std::collections::BTreeSet<_>>();

        for required in [
            "app.tick.started",
            "app.tick.completed",
            "source.work.started",
            "source.scan.progress",
            "source.scan.completed",
            "scheduler.retry.scheduled",
            "reconcile.apply.completed",
            "publication.publish.started",
            "publication.publish.completed",
            "publication.ack.failed",
            "rpc.call.started",
            "rpc.call.completed",
            "rpc.call.failed",
            "startup.stage.started",
            "startup.stage.completed",
        ] {
            assert!(
                event_names.contains(required),
                "missing required Reference log event: {required}"
            );
        }
    }

    #[test]
    fn reference_log_fields_use_layered_field_names() {
        let mut names = std::collections::BTreeSet::new();
        fields::ALL.iter().for_each(|field| {
            field.validate();
            assert!(
                names.insert(field.name),
                "duplicate log field: {}",
                field.name
            );
        });
    }

    #[test]
    fn reference_business_logs_do_not_hand_write_event_names() {
        let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut violations = Vec::new();
        collect_direct_event_literals(&src, &mut violations);

        assert!(
            violations.is_empty(),
            "Reference tracing logs must use logging::events constants; direct event literals: {violations:#?}"
        );
    }

    #[test]
    fn reference_business_logs_emit_event_layers_together() {
        let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut violations = Vec::new();
        collect_incomplete_layered_events(&src, &mut violations);

        assert!(
            violations.is_empty(),
            "Reference tracing logs using log_event.event must also emit component/area/action/outcome: {violations:#?}"
        );
    }

    #[test]
    fn reference_business_logs_use_declared_field_dictionary() {
        let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let known_fields = fields::ALL
            .iter()
            .map(|field| field.name)
            .collect::<std::collections::BTreeSet<_>>();
        let mut violations = Vec::new();
        collect_unknown_layered_log_fields(&src, &known_fields, &mut violations);

        assert!(
            violations.is_empty(),
            "Reference layered log fields must be declared in logging::fields::ALL: {violations:#?}"
        );
    }

    #[test]
    fn reference_business_logs_do_not_use_forbidden_field_aliases() {
        let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut violations = Vec::new();
        collect_forbidden_field_aliases(&src, &mut violations);

        assert!(
            violations.is_empty(),
            "Reference tracing logs must use canonical logging::fields names; forbidden field aliases: {violations:#?}"
        );
    }

    fn collect_direct_event_literals(path: &Path, violations: &mut Vec<String>) {
        if path.file_name().and_then(|name| name.to_str()) == Some("logging.rs") {
            return;
        }
        let metadata = std::fs::metadata(path).expect("reference source metadata is readable");
        if metadata.is_dir() {
            for entry in std::fs::read_dir(path).expect("reference source directory is readable") {
                let entry = entry.expect("reference source entry is readable");
                collect_direct_event_literals(&entry.path(), violations);
            }
            return;
        }
        if path.extension().and_then(|extension| extension.to_str()) != Some("rs") {
            return;
        }
        let source = std::fs::read_to_string(path).expect("reference source file is readable");
        for (line_index, line) in source.lines().enumerate() {
            let trimmed = line.trim();
            if !trimmed.contains("legacy_event") && trimmed.contains("event = \"") {
                violations.push(format!("{}:{}", path.display(), line_index + 1));
            }
        }
    }

    fn collect_unknown_layered_log_fields(
        path: &Path,
        known_fields: &std::collections::BTreeSet<&'static str>,
        violations: &mut Vec<String>,
    ) {
        if path.file_name().and_then(|name| name.to_str()) == Some("logging.rs") {
            return;
        }
        let metadata = std::fs::metadata(path).expect("reference source metadata is readable");
        if metadata.is_dir() {
            for entry in std::fs::read_dir(path).expect("reference source directory is readable") {
                let entry = entry.expect("reference source entry is readable");
                collect_unknown_layered_log_fields(&entry.path(), known_fields, violations);
            }
            return;
        }
        if path.extension().and_then(|extension| extension.to_str()) != Some("rs") {
            return;
        }
        let source = std::fs::read_to_string(path).expect("reference source file is readable");
        let lines = source.lines().collect::<Vec<_>>();
        for (line_index, line) in lines.iter().enumerate() {
            if !line.contains("event = log_event.event") {
                continue;
            }
            for (field_offset, field_line) in lines[line_index..].iter().enumerate() {
                if field_line.contains(");") {
                    break;
                }
                let trimmed = field_line.trim();
                let Some((field_name, _)) = trimmed.split_once('=') else {
                    continue;
                };
                let field_name = field_name.trim();
                if field_name.is_empty()
                    || !field_name
                        .chars()
                        .all(|ch| ch.is_ascii_alphanumeric() || ch == '_')
                {
                    continue;
                }
                if !known_fields.contains(field_name) {
                    violations.push(format!(
                        "{}:{} unknown field {field_name}",
                        path.display(),
                        line_index + field_offset + 1
                    ));
                }
            }
        }
    }

    fn collect_incomplete_layered_events(path: &Path, violations: &mut Vec<String>) {
        if path.file_name().and_then(|name| name.to_str()) == Some("logging.rs") {
            return;
        }
        let metadata = std::fs::metadata(path).expect("reference source metadata is readable");
        if metadata.is_dir() {
            for entry in std::fs::read_dir(path).expect("reference source directory is readable") {
                let entry = entry.expect("reference source entry is readable");
                collect_incomplete_layered_events(&entry.path(), violations);
            }
            return;
        }
        if path.extension().and_then(|extension| extension.to_str()) != Some("rs") {
            return;
        }
        let source = std::fs::read_to_string(path).expect("reference source file is readable");
        let lines = source.lines().collect::<Vec<_>>();
        for (line_index, line) in lines.iter().enumerate() {
            if !line.contains("event = log_event.event") {
                continue;
            }
            let context = lines[line_index..std::cmp::min(line_index + 8, lines.len())].join("\n");
            for required in [
                "component = log_event.component",
                "area = log_event.area",
                "action = log_event.action",
                "outcome = log_event.outcome",
            ] {
                if !context.contains(required) {
                    violations.push(format!(
                        "{}:{} missing {required}",
                        path.display(),
                        line_index + 1
                    ));
                }
            }
        }
    }

    fn collect_forbidden_field_aliases(path: &Path, violations: &mut Vec<String>) {
        if path.file_name().and_then(|name| name.to_str()) == Some("logging.rs") {
            return;
        }
        let metadata = std::fs::metadata(path).expect("reference source metadata is readable");
        if metadata.is_dir() {
            for entry in std::fs::read_dir(path).expect("reference source directory is readable") {
                let entry = entry.expect("reference source entry is readable");
                collect_forbidden_field_aliases(&entry.path(), violations);
            }
            return;
        }
        if path.extension().and_then(|extension| extension.to_str()) != Some("rs") {
            return;
        }
        let source = std::fs::read_to_string(path).expect("reference source file is readable");
        for (line_index, line) in source.lines().enumerate() {
            let trimmed = line.trim();
            for field in [
                "source_name",
                "provider_name",
                "provider_sync_error_message",
                "massive_options_page_count",
            ] {
                if trimmed.contains(&format!("{field} =")) {
                    violations.push(format!("{}:{} {field}", path.display(), line_index + 1));
                }
            }
        }
    }
}
