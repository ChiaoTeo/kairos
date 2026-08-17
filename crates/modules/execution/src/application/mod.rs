mod account_facts;
pub mod backtest;
mod intent_planner;
pub mod market_input;
mod model;
mod order_admission;
mod process;
mod risk_reservations;
mod service;

pub use account_facts::ExecutionAccountFacts;
pub use intent_planner::ExecutionIntentPlanner;
pub(crate) use model::remote_status;
pub use model::{
    CancelIntent, CancelOrder, DependencyWatermarks, ExecuteStrategyIntent, ExecutionAuditEvent,
    ExecutionAuditQuery, ExecutionBusinessChange, ExecutionBusinessEvent, ExecutionCurrentView,
    ExecutionError, ExecutionEvent, ExecutionFillReport, ExecutionOrderOptions, ExecutionSnapshot,
    ExpireIntent, HedgeRequirement, IntentEvent, IntentLegRequest, IntentState, IntentStatus,
    QuoteObservation, RefreshQuoteIntent, RemoteOrder, RemoteOrderQuery, ReplaceOrder,
    SnapshotWatermark, SubmitOrder, UnknownRemoteOrder, UnknownRemoteOrderResolution,
};
pub use order_admission::ExecutionOrderAdmission;
pub use process::{
    ExecutionAsyncRoute, ExecutionEventPublisher, ExecutionProcess, ExecutionSnapshotPublisher,
    IntentSnapshotPublisher,
};
pub use risk_reservations::{
    ExecutionRiskReservations, RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult,
};
pub(crate) use service::apply_connection_event;
pub use service::ExecutionApplication;

pub use backtest::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
    BacktestRunResult,
};
pub use market_input::{Bar, MarketObservation, Quote, QuoteBar, TradeBar};

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
    pub order_id: kairos_primitives::OrderId,
    pub symbol: kairos_primitives::Symbol,
    pub status: crate::domain::ExecutionOrderStatus,
    pub fill_quantity: Option<kairos_primitives::Quantity>,
    pub fill_price: Option<kairos_primitives::Price>,
    pub execution_id: Option<kairos_primitives::FillId>,
    pub fee_currency: Option<kairos_primitives::Currency>,
    pub fee_amount: Option<kairos_primitives::Money>,
    pub occurred_at_unix_nanos: kairos_primitives::UnixNanos,
    pub reason: String,
}
