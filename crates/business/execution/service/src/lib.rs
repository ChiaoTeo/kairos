//! Execution business boundary.
//!
//! Execution owns strategy intent and the exchange-facing order lifecycle.
//! Account owns account balances, positions and account-side order/fill facts;
//! the modules communicate through application-level commands and events.

pub mod application;
pub mod composition;
pub mod credentials;
pub mod domain;
mod services;

pub use application::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
    BacktestRunResult, CancelIntent, CancelOrder, DependencyWatermarks, ExecuteStrategyIntent,
    ExecutionApplication, ExecutionAuditEvent, ExecutionAuditQuery, ExecutionAuditSink,
    ExecutionError, ExecutionEvent, ExecutionFillReport, ExecutionOrderOptions, ExecutionProcess,
    ExecutionSnapshot, ExecutionSnapshotPublisher, ExpireIntent, HedgeRequirement, IntentEvent,
    IntentLegRequest, IntentSnapshotPublisher, IntentState, IntentStatus, QuoteObservation,
    RefreshQuoteIntent, ReplaceOrder, SnapshotWatermark, SubmitOrder, UnknownRemoteOrder,
    UnknownRemoteOrderResolution,
};
pub use application::market_input::{Bar, MarketObservation, Quote, QuoteBar, TradeBar};
pub use composition::{
    compose_execution_connections, compose_order_entry, ExecutionConnectionOptions,
    ExecutionConnections, ExecutionSimulator, FileExecutionStore, QueuedExecutionPreflight,
    SharedExecutionSnapshotPublisher, SharedIntentSnapshotPublisher, SimulatedOrderEntry,
    SimulationConfig, SimulationFill, SimulationOrder, SimulationOrderRequest,
    SimulationOrderStatus, SimulationResult, SocketExecutionPreflight,
};
pub use domain::{
    CompletionPolicy, ExecutionFill, ExecutionLeg, ExecutionOrder, ExecutionOrderStatus,
    ExecutionPlan, FailurePolicy, HedgePolicy, IntentLifecycle, IntentType, LegLifecycle,
    MakerExecutionPolicy, OrderSide, OrderType, SplitOrderPolicy,
};
pub use services::sqlx_audit::SqlxExecutionAudit;
pub use services::sqlx_persistence::SqlxExecutionStore;
