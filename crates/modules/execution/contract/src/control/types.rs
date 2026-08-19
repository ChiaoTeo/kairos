use kairos_primitives::{
    AccountId, DecisionId, DurationNanos, ExecutionRouteId, InstrumentId, IntentId, LegId,
    MarketId, OrderId, OrderOptionCode, OrderSide, OrderType, ParticipantId, Price,
    ProviderProductCode, ProviderSymbol, Quantity, Ratio, SegmentKey, SignedQuantity, StrategyId,
    UnixNanos,
};
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionControlResponse {
    pub status: Option<String>,
    pub command_id: Option<String>,
    pub intent_id: Option<String>,
    pub order_id: Option<String>,
    pub accepted: Option<bool>,
    pub error: Option<String>,
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionControlError {
    pub code: String,
    pub message: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRouteCandidateResponse {
    pub route_id: ExecutionRouteId,
    pub account_id: Option<AccountId>,
    pub segment_key: Option<SegmentKey>,
    pub instrument_id: Option<InstrumentId>,
    pub market_id: Option<MarketId>,
    pub participant_id: ParticipantId,
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
    pub supported_order_types: Vec<OrderType>,
    pub supported_options: Vec<OrderOptionCode>,
    pub ready: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRoutesResponse {
    pub routes: Vec<ExecutionRouteCandidateResponse>,
}

/// JSON control-plane values belong to UDS; they are not event or view models.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CommandEnvelope {
    pub command_id: Option<String>,
    pub idempotency_key: Option<String>,
    pub caller_id: Option<String>,
    pub workspace_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SubmitIntentRequest {
    #[serde(flatten)]
    pub envelope: CommandEnvelope,
    pub intent: ExecutionIntentRequest,
    #[serde(default)]
    pub admission_evidence: Option<IntentAdmissionEvidenceRequest>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentType {
    SingleOrder,
    TargetPosition,
    PairArbitrage,
    OptionSpread,
    PortfolioRebalance,
    QuoteProvisioning,
    Hedge,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum CompletionPolicy {
    #[default]
    AllLegsSatisfied,
    AllOrNothing,
    BestEffort,
    HedgeWithinTolerance,
    TargetQuantityReached,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum FailurePolicy {
    #[default]
    CancelRemaining,
    ContinueOtherLegs,
    Compensate,
    PauseForManualIntervention,
    MarkReconciliationRequired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SplitOrderPolicyRequest {
    pub max_child_quantity: Option<Quantity>,
    pub child_count: Option<u32>,
    pub min_child_quantity: Option<Quantity>,
    pub interval: Option<DurationNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MakerExecutionPolicyRequest {
    pub min_interval: Option<DurationNanos>,
    pub max_orders_per_window: Option<u32>,
    pub window: Option<DurationNanos>,
    pub max_inventory_abs: Option<SignedQuantity>,
    pub target_inventory: Option<SignedQuantity>,
    pub max_quote_age: Option<DurationNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionOrderOptionsRequest {
    pub time_in_force: Option<String>,
    pub reduce_only: Option<bool>,
    pub post_only: Option<bool>,
    pub position_side: Option<String>,
    pub quote_asset: Option<String>,
    pub wallet_type: Option<String>,
    pub trading_session: Option<String>,
    pub tokenize: Option<bool>,
    pub split: Option<SplitOrderPolicyRequest>,
    pub maker: Option<MakerExecutionPolicyRequest>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentLegRequest {
    pub leg_id: LegId,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    pub execution_route_id: Option<ExecutionRouteId>,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub limit_price: Option<Price>,
    pub target_position: bool,
    pub options: ExecutionOrderOptionsRequest,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct HedgePolicyRequest {
    pub leader_leg_id: LegId,
    pub hedge_leg_id: LegId,
    pub ratio: Ratio,
    pub contract_multiplier: Ratio,
    pub max_unhedged_quantity: Quantity,
    pub compensate_on_failure: bool,
    pub max_compensation_attempts: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionIntentRequest {
    pub intent_id: IntentId,
    pub strategy_decision_id: Option<DecisionId>,
    pub strategy_id: StrategyId,
    pub launch_id: kairos_primitives::runtime::LaunchId,
    pub instance_id: kairos_primitives::runtime::InstanceId,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    pub execution_route_id: Option<ExecutionRouteId>,
    pub account_ids: Vec<AccountId>,
    pub segment_key: SegmentKey,
    pub target_quantity: Quantity,
    pub limit_price: Option<Price>,
    pub source_snapshot_id: Option<String>,
    pub source_event_sequence: Option<kairos_primitives::Sequence>,
    pub source_event_time_unix_nanos: Option<UnixNanos>,
    pub reason: String,
    pub intent_type: IntentType,
    pub completion_policy: CompletionPolicy,
    pub failure_policy: FailurePolicy,
    pub legs: Vec<IntentLegRequest>,
    pub deadline_unix_nanos: Option<UnixNanos>,
    pub min_edge_bps: Option<u32>,
    pub max_slippage_bps: Option<u32>,
    pub estimated_fee_bps: Option<u32>,
    pub minimum_net_credit: Option<kairos_primitives::Money>,
    pub maximum_loss: Option<kairos_primitives::Money>,
    pub hedge_policy: Option<HedgePolicyRequest>,
    pub order_options: ExecutionOrderOptionsRequest,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentAdmissionEvidenceRequest {
    pub source: String,
    pub decision_id: DecisionId,
    pub outcome: String,
    pub original_intent: ExecutionIntentRequest,
    pub effective_intent: ExecutionIntentRequest,
    pub original_hash: String,
    pub effective_hash: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelOrderRequest {
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReplaceOrderRequest {
    pub quantity: Option<Quantity>,
    pub limit_price: Option<Price>,
    pub options: Option<ExecutionOrderOptionsRequest>,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReconcileExecutionRequest {
    pub account_id: Option<AccountId>,
    pub execution_route_id: Option<ExecutionRouteId>,
    pub order_id: Option<OrderId>,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionRestRequest {
    Health,
    Routes(ExecutionRoutesQuery),
    SubmitIntent(SubmitIntentRequest),
    CancelOrder {
        order_id: OrderId,
        request: CancelOrderRequest,
    },
    ReplaceOrder {
        order_id: OrderId,
        request: ReplaceOrderRequest,
    },
    Reconcile(ReconcileExecutionRequest),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionRestResponse {
    Health(Result<ExecutionHealthResponse, ExecutionControlError>),
    Routes(Result<ExecutionRoutesResponse, ExecutionControlError>),
    SubmitIntent(Result<ExecutionCommandStatus, ExecutionControlError>),
    CancelOrder(Result<ExecutionCommandStatus, ExecutionControlError>),
    ReplaceOrder(Result<ExecutionCommandStatus, ExecutionControlError>),
    Reconcile(Result<ExecutionReconcileResponse, ExecutionControlError>),
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRoutesQuery {
    pub account_id: Option<AccountId>,
    pub segment_key: Option<SegmentKey>,
    pub instrument_id: Option<InstrumentId>,
    pub market_id: Option<MarketId>,
    pub participant_id: Option<ParticipantId>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRouteHealth {
    pub route_id: ExecutionRouteId,
    pub status: String,
    pub required: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionHealthResponse {
    pub status: String,
    pub writer_recovery_ready: bool,
    pub outbox_backlog: u64,
    pub oldest_outbox_event_age_ms: Option<u64>,
    pub outbox_error: Option<String>,
    pub routes: Vec<ExecutionRouteHealth>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionCommandStatus {
    pub status: String,
    pub command_id: Option<String>,
    pub intent_id: Option<String>,
    pub order_id: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionReconcileResponse {
    pub changed: u64,
}
