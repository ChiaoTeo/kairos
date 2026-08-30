//! Execution business events.

use super::*;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionEvent {
    pub order_id: OrderId,
    #[serde(default)]
    pub intent_id: Option<IntentId>,
    #[serde(default)]
    pub plan_id: Option<PlanId>,
    #[serde(default)]
    pub leg_id: Option<LegId>,
    pub status: ExecutionOrderStatus,
    pub remote_order_id: Option<RemoteOrderId>,
    pub occurred_at_unix_nanos: UnixNanos,
    pub reason: String,
    #[serde(default)]
    pub fill_id: Option<FillId>,
    #[serde(default)]
    pub filled_quantity: Option<Quantity>,
    #[serde(default)]
    pub attempt: Option<ExecutionAttempt>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionBusinessEvent {
    pub sequence: Sequence,
    pub occurred_at_unix_nanos: UnixNanos,
    pub changes: Vec<ExecutionBusinessChange>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionBusinessChange {
    Intent {
        state: IntentState,
        event: IntentEvent,
    },
    Order {
        strategy_id: StrategyId,
        order: ExecutionOrder,
    },
    Fill {
        strategy_id: StrategyId,
        account_id: AccountId,
        intent_id: Option<IntentId>,
        market_id: Option<MarketId>,
        remote_order_id: Option<RemoteOrderId>,
        side: OrderSide,
        fill: ExecutionFill,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentEvent {
    pub intent_id: IntentId,
    #[serde(default)]
    pub strategy_decision_id: Option<DecisionId>,
    #[serde(default)]
    pub event_sequence: Sequence,
    #[serde(default)]
    pub previous_status: Option<IntentStatus>,
    pub status: IntentStatus,
    pub order_ids: Vec<OrderId>,
    pub completed_quantity: Quantity,
    pub occurred_at_unix_nanos: UnixNanos,
    pub reason: String,
    #[serde(default)]
    pub dependency_watermarks: DependencyWatermarks,
}
