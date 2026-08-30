pub mod backtest;
mod cli;
mod connected;
pub(crate) mod core;
mod model;
mod process;

kairos_execution_contract::execution_control_rpc_conflux_actor! {
    pub trait ExecutionRpcActor;
    service ExecutionRpcService;
}

pub use core::ExecutionApplication;

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
pub use model::{
    CancelIntent, CancelOrder, DependencyWatermarks, ExecuteStrategyIntent, ExecutionAuditEvent,
    ExecutionAuditQuery, ExecutionBusinessChange, ExecutionBusinessEvent, ExecutionCurrentView,
    ExecutionError, ExecutionEvent, ExecutionFillReport, ExecutionFundingRequirement,
    ExecutionOrderOptions, ExecutionRouteCandidate, ExecutionRouteQuery, ExecutionSnapshot,
    ExpireIntent, HedgeRequirement, IntentAdmissionEvidence, IntentEvent, IntentExecutionBenchmark,
    IntentLegRequest, IntentState, IntentStatus, QuoteObservation, QuoteRefreshPhase,
    QuoteRefreshTransaction, RefreshQuoteIntent, RemoteOrder, RemoteOrderQuery, RemoteOrderUpdate,
    RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult, SnapshotWatermark,
    SubmitOrder, UnknownRemoteOrder, UnknownRemoteOrderResolution,
};

pub use crate::domain::{
    AlgorithmAction, AlgorithmActionKind, AlgorithmActionStatus, AlgorithmChildCandidate,
    AlgorithmDecision, AlgorithmDecisionSequence, AlgorithmExecutionQuality,
    AlgorithmExecutionStyle, AlgorithmInput, AlgorithmLegBenchmark, AlgorithmLegBenchmarkQuality,
    AlgorithmLegExecutionQuality, AlgorithmLegLifecycle, AlgorithmLegRole, AlgorithmLegState,
    AlgorithmRun, AlgorithmRunId, AlgorithmRunStatus, CommitmentBasis, CommitmentResource,
    CommitmentStatus, CompletionPolicy, DeliveryCertainty, ExecutionAlgorithmPolicy,
    ExecutionAlgorithmSpec, ExecutionAttempt, ExecutionBenchmarkKind, ExecutionCommandKind,
    ExecutionFeeTotal, ExecutionFill, ExecutionLeg, ExecutionOrder, ExecutionOrderStatus,
    ExecutionPlan, FailurePolicy, HedgePolicy, IntentLifecycle, IntentType, LegLifecycle,
    MakerExecutionPolicy, MakerTakerHedgeSpec, NormalizedExposureLedger, OrderCommitment,
    OrderFactCursor, OrderReconciliationCause, OrderSide, OrderType, PassiveLimitPolicy,
    PassiveLimitSpec, RiskReservationSagaStatus, SelectedExecutionRoute, SplitOrderPolicy,
    TwapPolicy, TwapSpec, decide_immediate, decide_maker_taker_hedge, decide_passive_limit,
    decide_twap,
};
