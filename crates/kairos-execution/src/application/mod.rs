pub mod backtest;
pub mod protocol;
mod service;

pub use service::{
    CancelOrder, ExecuteIntent, ExecutionApplication, ExecutionAuditEvent, ExecutionAuditQuery,
    ExecutionError, ExecutionEvent, ExecutionFillReport, ExecutionOrderOptions, ExecutionSnapshot,
    RemoteOrder, RemoteOrderQuery, ReplaceOrder, SubmitOrder,
};

pub use backtest::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
};

pub trait ExecutionOrderQuery: Send {
    fn open_orders(&mut self, query: &RemoteOrderQuery) -> Result<Vec<RemoteOrder>, String>;
    fn history(&mut self, query: &RemoteOrderQuery) -> Result<Vec<RemoteOrder>, String>;
    fn detail(&mut self, query: &RemoteOrderQuery) -> Result<Option<RemoteOrder>, String>;
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

pub trait ExecutionStream: Send {
    fn next_event(&mut self) -> Result<Option<RemoteExecutionEvent>, String>;
}
