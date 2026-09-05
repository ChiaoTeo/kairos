//! Execution business boundary.
//!
//! Execution owns strategy intent and the exchange-facing order lifecycle.
//! Account owns account balances, positions and account-side order/fill facts;
//! other business packages communicate through owner contract crates only.

pub mod application;
pub mod composition;
mod domain;
mod services;

#[cfg(test)]
extern crate self as kairos_execution;
#[cfg(test)]
mod integration_tests;

pub use application::{
    AdmissionError, AdmissionRule, AlgorithmAction, AlgorithmActionKind, AlgorithmActionStatus,
    AlgorithmChildCandidate, AlgorithmDecision, AlgorithmDecisionSequence, AlgorithmError,
    AlgorithmExecutionQuality, AlgorithmExecutionStyle, AlgorithmInput, AlgorithmInvariant,
    AlgorithmLegBenchmark, AlgorithmLegBenchmarkQuality, AlgorithmLegExecutionQuality,
    AlgorithmLegLifecycle, AlgorithmLegRole, AlgorithmLegState, AlgorithmRun, AlgorithmRunId,
    AlgorithmRunStatus, BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics,
    BacktestRequest, BacktestRunResult, Bar, CancelIntent, CancelOrder, CliExecutionApplication,
    CliExecutionOutput, CommitmentBasis, CommitmentResource, CommitmentStatus, CompletionPolicy,
    ConnectedExecutionApplication, ConnectedExecutionOutput, DeliveryCertainty,
    DependencyWatermarks, ExecuteStrategyIntent, ExecutionAlgorithmPolicy, ExecutionAlgorithmSpec,
    ExecutionApplication, ExecutionAttempt, ExecutionAuditEvent, ExecutionAuditQuery,
    ExecutionBenchmarkKind, ExecutionCommandKind, ExecutionError, ExecutionEvent,
    ExecutionFeeTotal, ExecutionFill, ExecutionFillReport, ExecutionLeg, ExecutionOrder,
    ExecutionOrderOptions, ExecutionOrderStatus, ExecutionPlan, ExecutionRuntimeError,
    ExecutionSnapshot, ExpireIntent, FailurePolicy, HedgePolicy, HedgeRequirement, IntentError,
    IntentEvent, IntentExecutionBenchmark, IntentLegRequest, IntentLifecycle, IntentState,
    IntentStatus, IntentType, LegLifecycle, MakerExecutionPolicy, MakerTakerHedgeSpec,
    MarketObservation, MarketObservationError, NormalizedExposureLedger, ObservationScope,
    OrderCommitment, OrderError, OrderFactCursor, OrderReconciliationCause, OrderSide, OrderType,
    PassiveLimitPolicy, PassiveLimitSpec, Quote, QuoteBar, QuoteObservation, QuoteRefreshPhase,
    QuoteRefreshTransaction, RefreshQuoteIntent, RiskReservationSagaStatus, RouteConstraintFailure,
    SnapshotWatermark, SplitOrderPolicy, SubmitOrder, TradeBar, TwapPolicy, TwapSpec,
    UnknownRemoteOrder, UnknownRemoteOrderResolution, decide_immediate, decide_maker_taker_hedge,
    decide_passive_limit, decide_twap,
};
