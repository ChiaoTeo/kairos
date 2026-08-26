//! Execution business boundary.
//!
//! Execution owns strategy intent and the exchange-facing order lifecycle.
//! Account owns account balances, positions and account-side order/fill facts;
//! the modules communicate through application-level commands and events.

pub mod application;
pub mod composition;
mod domain;
mod services;

#[cfg(test)]
extern crate self as kairos_execution;
#[cfg(test)]
mod integration_tests;

pub use application::{
    AlgorithmAction, AlgorithmActionKind, AlgorithmActionStatus, AlgorithmChildCandidate,
    AlgorithmDecision, AlgorithmExecutionQuality, AlgorithmExecutionStyle, AlgorithmInput,
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
    ExecutionOrderOptions, ExecutionOrderStatus, ExecutionPlan, ExecutionSnapshot, ExpireIntent,
    FailurePolicy, HedgePolicy, HedgeRequirement, IntentEvent, IntentExecutionBenchmark,
    IntentLegRequest, IntentLifecycle, IntentState, IntentStatus, IntentType, LegLifecycle,
    MakerExecutionPolicy, MakerTakerHedgeSpec, MarketObservation, NormalizedExposureLedger,
    ObservationScope, OrderCommitment, OrderFactCursor, OrderReconciliationCause, OrderSide,
    OrderType, PassiveLimitPolicy, PassiveLimitSpec, Quote, QuoteBar, QuoteObservation,
    QuoteRefreshPhase, QuoteRefreshTransaction, RefreshQuoteIntent, RiskReservationSagaStatus,
    SnapshotWatermark, SplitOrderPolicy, SubmitOrder, TradeBar, TwapPolicy, TwapSpec,
    UnknownRemoteOrder, UnknownRemoteOrderResolution, decide_immediate, decide_maker_taker_hedge,
    decide_passive_limit, decide_twap,
};
