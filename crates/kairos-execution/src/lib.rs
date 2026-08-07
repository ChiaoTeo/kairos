//! Execution business boundary.
//!
//! Execution owns the exchange-facing order lifecycle. Account owns the
//! business order and fill facts; the two modules communicate through
//! application-level commands and events at composition boundaries.

pub mod application;
pub mod composition;
pub mod credentials;
pub mod domain;
mod services;

pub use application::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
    CancelOrder, ExecuteIntent, ExecutionApplication, ExecutionAuditEvent, ExecutionAuditQuery,
    ExecutionAuditSink, ExecutionError, ExecutionEvent, ExecutionFillReport, ExecutionOrderOptions,
    ExecutionProcess, ExecutionSnapshot, ReplaceOrder, SubmitOrder,
};
pub use composition::{
    compose_order_entry, ExecutionConnectionOptions, FileExecutionStore, SimulatedOrderEntry,
    SqliteExecutionAudit,
};
pub use domain::{ExecutionFill, ExecutionOrder, ExecutionOrderStatus, OrderSide, OrderType};
