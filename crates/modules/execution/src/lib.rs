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
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
    BacktestRunResult, Bar, CancelIntent, CancelOrder, CommitmentBasis, CommitmentResource,
    CommitmentStatus, CompletionPolicy, DependencyWatermarks, ExecuteStrategyIntent,
    ExecutionApplication, ExecutionAuditEvent, ExecutionAuditQuery, ExecutionError, ExecutionEvent,
    ExecutionFill, ExecutionFillReport, ExecutionLeg, ExecutionOrder, ExecutionOrderOptions,
    ExecutionOrderStatus, ExecutionPlan, ExecutionSnapshot, ExpireIntent, FailurePolicy,
    HedgePolicy, HedgeRequirement, IntentEvent, IntentLegRequest, IntentLifecycle, IntentState,
    IntentStatus, IntentType, LegLifecycle, MakerExecutionPolicy, MarketObservation,
    OrderCommitment, OrderSide, OrderType, Quote, QuoteBar, QuoteObservation, RefreshQuoteIntent,
    ReplaceOrder, RiskReservationSagaStatus, SnapshotWatermark, SplitOrderPolicy, SubmitOrder,
    TradeBar, UnknownRemoteOrder, UnknownRemoteOrderResolution,
};

#[cfg(test)]
pub(crate) use application::ExecutionProcess;
