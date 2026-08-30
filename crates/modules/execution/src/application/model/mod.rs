//! Application errors plus domain-owned Execution request and result vocabulary.

mod error;

pub use error::*;

pub(crate) use crate::domain::remote_status;
pub use crate::domain::{
    CancelIntent, CancelOrder, DependencyWatermarks, ExecuteStrategyIntent, ExecutionAuditEvent,
    ExecutionAuditQuery, ExecutionBusinessChange, ExecutionBusinessEvent, ExecutionCurrentView,
    ExecutionEvent, ExecutionFillReport, ExecutionFundingRequirement, ExecutionOrderOptions,
    ExecutionRouteCandidate, ExecutionRouteQuery, ExecutionSnapshot, ExpireIntent,
    HedgeRequirement, IntentAdmissionEvidence, IntentEvent, IntentExecutionBenchmark,
    IntentLegRequest, IntentState, IntentStatus, QuoteObservation, QuoteRefreshPhase,
    QuoteRefreshTransaction, RefreshQuoteIntent, RemoteOrder, RemoteOrderQuery, RemoteOrderUpdate,
    RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult, SnapshotWatermark,
    SubmitOrder, UnknownRemoteOrder, UnknownRemoteOrderResolution,
};
