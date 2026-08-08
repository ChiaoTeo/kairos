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
    CancelOrder, ExecuteStrategyIntent, ExecutionApplication, ExecutionAuditEvent,
    ExecutionAuditQuery, ExecutionAuditSink, ExecutionError, ExecutionEvent, ExecutionFillReport,
    ExecutionOrderOptions, ExecutionProcess, ExecutionSnapshot, IntentEvent, IntentState,
    IntentStatus, ReplaceOrder, SubmitOrder,
};
pub use composition::{
    compose_order_entry, ExecutionConnectionOptions, FileExecutionStore,
    SharedExecutionSnapshotPublisher, SharedIntentSnapshotPublisher, SimulatedOrderEntry,
    SocketExecutionPreflight, SqliteExecutionAudit,
};
pub use domain::{ExecutionFill, ExecutionOrder, ExecutionOrderStatus, OrderSide, OrderType};
