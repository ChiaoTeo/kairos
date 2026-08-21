pub mod backtest;
mod conflux;
pub(crate) mod core;
mod model;

kairos_execution_contract::execution_control_rpc_conflux_actor! {
    pub trait ExecutionRpcActor;
    service ExecutionRpcService;
}

pub use core::ExecutionApplication;
pub(crate) use core::apply_connection_event;

pub use backtest::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest,
    BacktestRunResult, Bar, MarketObservation, ObservationScope, Quote, QuoteBar, TradeBar,
};
pub(crate) use model::remote_status;
pub use model::{
    CancelIntent, CancelOrder, DependencyWatermarks, ExecuteStrategyIntent,
    ExecutionBusinessChange, ExecutionBusinessEvent, ExecutionCurrentView, ExecutionError,
    ExecutionEvent, ExecutionFillReport, ExecutionFundingRequirement, ExecutionOrderOptions,
    ExecutionRouteCandidate, ExecutionRouteQuery, ExecutionSnapshot, ExpireIntent,
    HedgeRequirement, IntentAdmissionEvidence, IntentEvent, IntentLegRequest, IntentState,
    IntentStatus, QuoteObservation, RefreshQuoteIntent, RemoteOrder, RemoteOrderQuery,
    ReplaceOrder, RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult,
    SnapshotWatermark, SubmitOrder, UnknownRemoteOrder, UnknownRemoteOrderResolution,
};

pub use crate::domain::{
    CommitmentBasis, CommitmentResource, CommitmentStatus, CompletionPolicy, ExecutionFill,
    ExecutionLeg, ExecutionOrder, ExecutionOrderStatus, ExecutionPlan, FailurePolicy, HedgePolicy,
    IntentLifecycle, IntentType, LegLifecycle, MakerExecutionPolicy, OrderCommitment, OrderSide,
    OrderType, RiskReservationSagaStatus, SelectedExecutionRoute, SplitOrderPolicy,
};
pub use crate::services::audit::{ExecutionAuditEvent, ExecutionAuditQuery};
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RemoteOrderUpdate {
    pub order_id: kairos_primitives::execution::OrderId,
    pub symbol: kairos_primitives::reference::Symbol,
    pub status: crate::domain::ExecutionOrderStatus,
    pub fill_quantity: Option<kairos_primitives::decimal::Quantity>,
    pub fill_price: Option<kairos_primitives::decimal::Price>,
    pub execution_id: Option<kairos_primitives::execution::FillId>,
    pub fee_currency: Option<kairos_primitives::reference::Currency>,
    pub fee_amount: Option<kairos_primitives::decimal::Money>,
    pub occurred_at_unix_nanos: kairos_primitives::time::UnixNanos,
    pub reason: String,
}
