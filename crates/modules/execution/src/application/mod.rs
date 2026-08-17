pub mod backtest;
pub(crate) mod core;
mod model;
mod process;

pub use crate::domain::{
    CommitmentBasis, CommitmentResource, CommitmentStatus, CompletionPolicy, ExecutionFill,
    ExecutionLeg, ExecutionOrder, ExecutionOrderStatus, ExecutionPlan, FailurePolicy, HedgePolicy,
    IntentLifecycle, IntentType, LegLifecycle, MakerExecutionPolicy, OrderCommitment, OrderSide,
    OrderType, RiskReservationSagaStatus, SplitOrderPolicy,
};
pub use crate::services::audit::{ExecutionAuditEvent, ExecutionAuditQuery};
pub use backtest::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
    BacktestRunResult, Bar, MarketObservation, Quote, QuoteBar, TradeBar,
};
pub(crate) use core::apply_connection_event;
pub use core::ExecutionApplication;
pub(crate) use model::remote_status;
pub use model::{
    CancelIntent, CancelOrder, DependencyWatermarks, ExecuteStrategyIntent,
    ExecutionBusinessChange, ExecutionBusinessEvent, ExecutionCurrentView, ExecutionError,
    ExecutionEvent, ExecutionFillReport, ExecutionOrderOptions, ExecutionSnapshot, ExpireIntent,
    HedgeRequirement, IntentEvent, IntentLegRequest, IntentState, IntentStatus, QuoteObservation,
    RefreshQuoteIntent, RemoteOrder, RemoteOrderQuery, ReplaceOrder, RiskAuthorizationContext,
    RiskCommandFailure, RiskCommandResult, SnapshotWatermark, SubmitOrder, UnknownRemoteOrder,
    UnknownRemoteOrderResolution,
};
pub use process::{ExecutionAsyncRoute, ExecutionProcess};
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
