pub mod backtest;
mod preflight;
mod process;
mod service;

pub use preflight::ExecutionPreflight;
pub use process::ExecutionProcess;
pub use service::{
    CancelOrder, DependencyWatermarks, ExecuteStrategyIntent, ExecutionApplication,
    ExecutionAuditEvent, ExecutionAuditQuery, ExecutionError, ExecutionEvent, ExecutionFillReport,
    ExecutionOrderOptions, ExecutionSnapshot, IntentEvent, IntentState, IntentStatus, RemoteOrder,
    RemoteOrderQuery, ReplaceOrder, SnapshotWatermark, SubmitOrder,
};

pub use backtest::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
};

pub trait ExecutionAuditSink: Send {
    fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String>;
    fn publish_intent(&mut self, _event: &IntentEvent) -> Result<(), String> {
        Ok(())
    }
    fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intent_events: &[IntentEvent],
    ) -> Result<(), String> {
        for event in events {
            self.publish(event)?;
        }
        for event in intent_events {
            self.publish_intent(event)?;
        }
        Ok(())
    }
    fn query(&mut self, query: &ExecutionAuditQuery) -> Result<Vec<ExecutionAuditEvent>, String>;
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct VenueOrderUpdate {
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
