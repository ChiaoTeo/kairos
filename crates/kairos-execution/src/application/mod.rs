pub mod backtest;
mod process;
pub(crate) mod protocol;
mod service;

pub use process::ExecutionProcess;
pub use protocol::ExecutionStateStore;
pub use service::{
    CancelOrder, ExecuteIntent, ExecutionApplication, ExecutionAuditEvent, ExecutionAuditQuery,
    ExecutionError, ExecutionEvent, ExecutionFillReport, ExecutionOrderOptions, ExecutionSnapshot,
    RemoteOrder, RemoteOrderQuery, ReplaceOrder, SubmitOrder,
};

pub use backtest::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
};

pub trait ExecutionAuditSink: Send {
    fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String>;
    fn query(&mut self, query: &ExecutionAuditQuery) -> Result<Vec<ExecutionAuditEvent>, String>;
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RemoteExecutionEvent {
    pub order_id: String,
    pub symbol: String,
    pub status: String,
    pub fill_quantity: Option<String>,
    pub fill_price: Option<String>,
    pub execution_id: Option<String>,
    pub fee_currency: Option<String>,
    pub fee_amount: Option<String>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
}
