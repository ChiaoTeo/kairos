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
    AlgorithmDecision, AlgorithmExecutionStyle, AlgorithmInput, AlgorithmLegLifecycle,
    AlgorithmLegRole, AlgorithmLegState, AlgorithmRun, AlgorithmRunId, AlgorithmRunStatus,
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
    BacktestRunResult, Bar, CancelIntent, CancelOrder, CliExecutionApplication, CliExecutionOutput,
    CommitmentBasis, CommitmentResource, CommitmentStatus, CompletionPolicy,
    ConnectedExecutionApplication, ConnectedExecutionOutput, DependencyWatermarks,
    ExecuteStrategyIntent, ExecutionAlgorithmPolicy, ExecutionAlgorithmSpec, ExecutionApplication,
    ExecutionAuditEvent, ExecutionAuditQuery, ExecutionError, ExecutionEvent, ExecutionFill,
    ExecutionFillReport, ExecutionLeg, ExecutionOrder, ExecutionOrderOptions, ExecutionOrderStatus,
    ExecutionPlan, ExecutionSnapshot, ExpireIntent, FailurePolicy, HedgePolicy, HedgeRequirement,
    IntentEvent, IntentLegRequest, IntentLifecycle, IntentState, IntentStatus, IntentType,
    LegLifecycle, MakerExecutionPolicy, MakerTakerHedgeSpec, MarketObservation,
    NormalizedExposureLedger, ObservationScope, OrderCommitment, OrderSide, OrderType, Quote,
    QuoteBar, QuoteObservation, RefreshQuoteIntent, ReplaceOrder, RiskReservationSagaStatus,
    SnapshotWatermark, SplitOrderPolicy, SubmitOrder, TradeBar, TwapPolicy, TwapSpec,
    UnknownRemoteOrder, UnknownRemoteOrderResolution, decide_immediate, decide_maker_taker_hedge,
    decide_twap,
};
