//! Public Execution business events.

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
        strategy_id: String,
        order: ExecutionOrder,
    },
    Fill {
        strategy_id: String,
        account_id: String,
        intent_id: Option<String>,
        market_id: Option<String>,
        remote_order_id: Option<String>,
        side: OrderSide,
        fill: ExecutionFill,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentEvent {
    pub intent_id: IntentId,
    #[serde(default)]
    pub strategy_decision_id: Option<String>,
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
