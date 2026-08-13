pub mod backtest;
mod preflight;
mod process;
mod service;

pub use preflight::ExecutionPreflight;
pub use process::{
    ExecutionAsyncRoute, ExecutionEventPublisher, ExecutionProcess, ExecutionSnapshotPublisher,
    IntentSnapshotPublisher,
};
pub(crate) use service::remote_status;
pub use service::{
    CancelIntent, CancelOrder, DependencyWatermarks, ExecuteStrategyIntent, ExecutionApplication,
    ExecutionAuditEvent, ExecutionAuditQuery, ExecutionBusinessChange, ExecutionBusinessEvent,
    ExecutionCurrentView, ExecutionError, ExecutionEvent, ExecutionFillReport,
    ExecutionOrderOptions, ExecutionSnapshot, ExpireIntent, HedgeRequirement, IntentEvent,
    IntentLegRequest, IntentState, IntentStatus, QuoteObservation, RefreshQuoteIntent, RemoteOrder,
    RemoteOrderQuery, ReplaceOrder, SnapshotWatermark, SubmitOrder, UnknownRemoteOrder,
    UnknownRemoteOrderResolution,
};

pub use backtest::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
    BacktestRunResult,
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
pub struct RemoteOrderUpdate {
    pub order_id: kairos_domain_types::OrderId,
    pub symbol: kairos_domain_types::Symbol,
    pub status: crate::domain::ExecutionOrderStatus,
    pub fill_quantity: Option<kairos_domain_types::Quantity>,
    pub fill_price: Option<kairos_domain_types::Price>,
    pub execution_id: Option<kairos_domain_types::FillId>,
    pub fee_currency: Option<kairos_domain_types::Currency>,
    pub fee_amount: Option<kairos_domain_types::Money>,
    pub occurred_at_unix_nanos: kairos_domain_types::UnixNanos,
    pub reason: String,
}
