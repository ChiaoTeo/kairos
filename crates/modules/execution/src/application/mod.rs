pub mod backtest;
mod cli;
mod conflux;
mod connected;
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
pub use cli::{
    CliExecutionApplication, CliExecutionCommandResult, CliExecutionContext, CliExecutionFill,
    CliExecutionFillsResult, CliExecutionOrder, CliExecutionOrderResult, CliExecutionOrdersResult,
    CliExecutionOutcome, CliExecutionOutput, CliExecutionReplaceResult, StandaloneExecutionBinding,
};
pub use connected::{ConnectedExecutionApplication, ConnectedExecutionOutput};
pub(crate) use model::remote_status;
pub use model::{
    CancelIntent, CancelOrder, DependencyWatermarks, ExecuteStrategyIntent,
    ExecutionBusinessChange, ExecutionBusinessEvent, ExecutionCurrentView, ExecutionError,
    ExecutionEvent, ExecutionFillReport, ExecutionFundingRequirement, ExecutionOrderOptions,
    ExecutionRouteCandidate, ExecutionRouteQuery, ExecutionSnapshot, ExpireIntent,
    HedgeRequirement, IntentAdmissionEvidence, IntentEvent, IntentExecutionBenchmark,
    IntentLegRequest, IntentState, IntentStatus, QuoteObservation, QuoteRefreshPhase,
    QuoteRefreshTransaction, RefreshQuoteIntent, RemoteOrder, RemoteOrderQuery,
    RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult, SnapshotWatermark,
    SubmitOrder, UnknownRemoteOrder, UnknownRemoteOrderResolution,
};

pub use crate::domain::{
    AlgorithmAction, AlgorithmActionKind, AlgorithmActionStatus, AlgorithmChildCandidate,
    AlgorithmDecision, AlgorithmExecutionQuality, AlgorithmExecutionStyle, AlgorithmInput,
    AlgorithmLegBenchmark, AlgorithmLegBenchmarkQuality, AlgorithmLegExecutionQuality,
    AlgorithmLegLifecycle, AlgorithmLegRole, AlgorithmLegState, AlgorithmRun, AlgorithmRunId,
    AlgorithmRunStatus, CommitmentBasis, CommitmentResource, CommitmentStatus, CompletionPolicy,
    DeliveryCertainty, ExecutionAlgorithmPolicy, ExecutionAlgorithmSpec, ExecutionAttempt,
    ExecutionBenchmarkKind, ExecutionCommandKind, ExecutionFeeTotal, ExecutionFill, ExecutionLeg,
    ExecutionOrder, ExecutionOrderStatus, ExecutionPlan, FailurePolicy, HedgePolicy,
    IntentLifecycle, IntentType, LegLifecycle, MakerExecutionPolicy, MakerTakerHedgeSpec,
    NormalizedExposureLedger, OrderCommitment, OrderFactCursor, OrderReconciliationCause,
    OrderSide, OrderType, PassiveLimitPolicy, PassiveLimitSpec, RiskReservationSagaStatus,
    SelectedExecutionRoute, SplitOrderPolicy, TwapPolicy, TwapSpec, decide_immediate,
    decide_maker_taker_hedge, decide_passive_limit, decide_twap,
};
pub use crate::services::audit::{ExecutionAuditEvent, ExecutionAuditQuery};
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RemoteOrderUpdate {
    pub remote_order_id: kairos_primitives::integration::RemoteOrderId,
    pub client_order_id: Option<kairos_primitives::execution::ClientOrderId>,
    pub symbol: kairos_primitives::reference::Symbol,
    pub status: crate::domain::ExecutionOrderStatus,
    pub fill_quantity: Option<kairos_primitives::decimal::Quantity>,
    pub fill_price: Option<kairos_primitives::decimal::Price>,
    pub execution_id: Option<kairos_primitives::execution::FillId>,
    pub fee_currency: Option<kairos_primitives::reference::Currency>,
    pub fee_amount: Option<kairos_primitives::decimal::Money>,
    pub source_cursor: Option<crate::domain::OrderFactCursor>,
    pub occurred_at_unix_nanos: kairos_primitives::time::UnixNanos,
    pub reason: String,
}
