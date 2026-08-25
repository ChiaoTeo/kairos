use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::decimal::{Money, Price, Quantity, Rate, Ratio, SignedQuantity};
use kairos_primitives::execution::{
    ExecutionChannelCode, ExecutionRouteId, FillId, IntentId, LegId, OrderEntrySymbol, OrderId,
    OrderOptionCode, OrderSide, OrderType,
};
use kairos_primitives::reference::{Currency, InstrumentId, MarketId};
use kairos_primitives::risk::DecisionId;
use kairos_primitives::runtime::{ActorId, IdempotencyKey, RequestId, StrategyId, WorkspaceId};
use kairos_primitives::time::{DurationNanos, UnixNanos};
use kairos_protocol::control::jsonrpc::{RpcResult, conflux_rpc};
use serde::{Deserialize, Serialize};

#[conflux_rpc(namespace = "execution")]
pub trait ExecutionControlRpc {
    async fn health(&self) -> RpcResult<kairos_execution_contract::ExecutionHealthResponse>;

    async fn routes(
        &self,
        query: kairos_execution_contract::ExecutionRoutesQuery,
    ) -> RpcResult<kairos_execution_contract::ExecutionRoutesResponse>;

    async fn submit_intent(
        &self,
        request: kairos_execution_contract::SubmitIntentRequest,
    ) -> RpcResult<kairos_execution_contract::ExecutionCommandStatus>;

    async fn cancel_order(
        &self,
        order_id: kairos_primitives::execution::OrderId,
        request: kairos_execution_contract::CancelOrderRequest,
    ) -> RpcResult<kairos_execution_contract::ExecutionCommandStatus>;

    async fn replace_order(
        &self,
        order_id: kairos_primitives::execution::OrderId,
        request: kairos_execution_contract::ReplaceOrderRequest,
    ) -> RpcResult<kairos_execution_contract::ExecutionCommandStatus>;

    async fn reconcile(
        &self,
        request: kairos_execution_contract::ReconcileExecutionRequest,
    ) -> RpcResult<kairos_execution_contract::ExecutionReconcileResponse>;

    async fn advance_time(
        &self,
        request: kairos_execution_contract::AdvanceExecutionTimeRequest,
    ) -> RpcResult<kairos_execution_contract::AdvanceExecutionTimeResponse>;

    async fn backtest_run(
        &self,
        request: kairos_execution_contract::ExecutionBacktestRequest,
    ) -> RpcResult<kairos_execution_contract::ExecutionBacktestRunResponse>;

    async fn backtest_market(
        &self,
        request: kairos_execution_contract::ExecutionBacktestMarketRequest,
    ) -> RpcResult<kairos_execution_contract::ExecutionBacktestMarketResponse>;
}
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionControlResponse {
    pub status: Option<String>,
    pub command_id: Option<RequestId>,
    pub intent_id: Option<IntentId>,
    pub order_id: Option<OrderId>,
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
    pub broker_id: BrokerId,
    pub execution_channel: ExecutionChannelCode,
    #[serde(alias = "provider_symbol")]
    pub order_entry_symbol: OrderEntrySymbol,
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
    pub command_id: Option<RequestId>,
    pub idempotency_key: Option<IdempotencyKey>,
    pub caller_id: Option<ActorId>,
    pub workspace_id: Option<WorkspaceId>,
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
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TwapPolicyRequest {
    pub slice_count: u32,
    pub slice_interval: DurationNanos,
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
    pub max_unhedged_duration: Option<DurationNanos>,
    #[serde(default)]
    pub fallback_execution_route_ids: Vec<ExecutionRouteId>,
    pub compensate_on_failure: bool,
    pub max_compensation_attempts: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", content = "policy", rename_all = "snake_case")]
pub enum ExecutionAlgorithmPolicyRequest {
    Immediate,
    Twap(TwapPolicyRequest),
    MakerTakerHedge(HedgePolicyRequest),
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
    pub source_event_sequence: Option<kairos_primitives::time::Sequence>,
    pub source_event_time_unix_nanos: Option<UnixNanos>,
    pub reason: String,
    pub intent_type: IntentType,
    pub algorithm: ExecutionAlgorithmPolicyRequest,
    pub completion_policy: CompletionPolicy,
    pub failure_policy: FailurePolicy,
    pub legs: Vec<IntentLegRequest>,
    pub deadline_unix_nanos: Option<UnixNanos>,
    pub min_edge_bps: Option<u32>,
    pub max_slippage_bps: Option<u32>,
    pub estimated_fee_bps: Option<u32>,
    pub minimum_net_credit: Option<kairos_primitives::decimal::Money>,
    pub maximum_loss: Option<kairos_primitives::decimal::Money>,
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

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceExecutionTimeRequest {
    pub event_time_unix_nanos: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceExecutionTimeResponse {
    pub advanced_to_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestEquityPoint {
    pub observed_at_unix_nanos: UnixNanos,
    pub equity: Money,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestFill {
    pub instrument_id: InstrumentId,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub price: Price,
    #[serde(default)]
    pub fee: Money,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ExecutionBacktestObservationScope {
    Market {
        market_id: String,
    },
    Consolidated {
        instrument_id: String,
        network_id: Option<String>,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestQuote {
    pub scope: ExecutionBacktestObservationScope,
    pub instrument_id: String,
    pub bid_price: Option<String>,
    pub bid_quantity: Option<String>,
    pub ask_price: Option<String>,
    pub ask_quantity: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestBar {
    pub scope: ExecutionBacktestObservationScope,
    pub instrument_id: String,
    pub timeframe: String,
    pub open: String,
    pub high: String,
    pub low: String,
    pub close: String,
    pub volume: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
    pub derivation: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestTradeBar {
    pub bar: ExecutionBacktestBar,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestQuoteBar {
    pub bar: ExecutionBacktestBar,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExecutionBacktestMarketObservation {
    Quote(ExecutionBacktestQuote),
    Bar(ExecutionBacktestBar),
    TradeBar(ExecutionBacktestTradeBar),
    QuoteBar(ExecutionBacktestQuoteBar),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestSimulationConfig {
    #[serde(default)]
    pub fee_bps: Rate,
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    #[serde(default)]
    pub slippage_bps: Rate,
    #[serde(default = "default_true")]
    pub enforce_quote_quantity: bool,
}

impl Default for ExecutionBacktestSimulationConfig {
    fn default() -> Self {
        Self {
            fee_bps: Rate::ZERO,
            fee_currency: None,
            slippage_bps: Rate::ZERO,
            enforce_quote_quantity: true,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestOrderRequest {
    pub order_id: OrderId,
    pub instrument_id: InstrumentId,
    #[serde(default)]
    pub market_id: Option<MarketId>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Quantity,
    #[serde(default)]
    pub limit_price: Option<Price>,
    pub submitted_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExecutionBacktestOrderStatus {
    Accepted,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestOrder {
    pub request: ExecutionBacktestOrderRequest,
    pub status: ExecutionBacktestOrderStatus,
    pub filled_quantity: Quantity,
    pub remaining_quantity: Quantity,
    pub updated_at_unix_nanos: UnixNanos,
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestSimulationFill {
    pub fill_id: FillId,
    pub order_id: OrderId,
    pub instrument_id: InstrumentId,
    #[serde(default)]
    pub execution_market_id: Option<MarketId>,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub price: Price,
    pub fee: Money,
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestRequest {
    pub initial_equity: Money,
    #[serde(default)]
    pub equity_curve: Vec<ExecutionBacktestEquityPoint>,
    #[serde(default)]
    pub fills: Vec<ExecutionBacktestFill>,
    #[serde(default)]
    pub risk_free_rate: Rate,
    pub annualization_periods: Option<f64>,
    #[serde(default)]
    pub market_events: Vec<ExecutionBacktestMarketObservation>,
    #[serde(default)]
    pub orders: Vec<ExecutionBacktestOrderRequest>,
    #[serde(default)]
    pub simulation: ExecutionBacktestSimulationConfig,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestMetrics {
    pub trade_count: usize,
    pub win_count: usize,
    pub loss_count: usize,
    pub win_rate: String,
    pub gross_profit: String,
    pub gross_loss: String,
    pub net_profit: String,
    pub max_drawdown: String,
    pub max_drawdown_pct: String,
    pub sharpe: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestRunResponse {
    pub metrics: ExecutionBacktestMetrics,
    pub orders: Vec<ExecutionBacktestOrder>,
    pub fills: Vec<ExecutionBacktestSimulationFill>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestMarketRequest {
    pub event: ExecutionBacktestMarketObservation,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct ExecutionBacktestMarketResponse {
    pub fills: Vec<ExecutionBacktestSimulationFill>,
}

fn default_true() -> bool {
    true
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRoutesQuery {
    pub account_id: Option<AccountId>,
    pub segment_key: Option<SegmentKey>,
    pub instrument_id: Option<InstrumentId>,
    pub market_id: Option<MarketId>,
    pub broker_id: Option<BrokerId>,
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
    pub command_id: Option<RequestId>,
    pub intent_id: Option<IntentId>,
    pub order_id: Option<OrderId>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionReconcileResponse {
    pub changed: u64,
}
