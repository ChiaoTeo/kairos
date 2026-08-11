//! Public Execution snapshot models.

use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum OrderType {
    Market,
    Limit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ExecutionOrderStatus {
    Pending,
    Submitting,
    Accepted,
    PartiallyFilled,
    Filled,
    CancelRequested,
    Canceled,
    Rejected,
    Expired,
    Unknown,
    Failed,
}

impl ExecutionOrderStatus {
    pub fn terminal(self) -> bool {
        matches!(
            self,
            Self::Filled | Self::Canceled | Self::Rejected | Self::Expired
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionOrder {
    pub order_id: String,
    #[serde(default)]
    pub plan_id: Option<String>,
    #[serde(default)]
    pub leg_id: Option<String>,
    pub intent_id: Option<String>,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity_mantissa: i64,
    pub quantity_scale: u8,
    pub limit_price_mantissa: Option<i64>,
    pub limit_price_scale: Option<u8>,
    pub remote_order_id: Option<String>,
    pub filled_quantity_mantissa: i64,
    pub filled_quantity_scale: u8,
    pub status: ExecutionOrderStatus,
    pub submitted_at_unix_nanos: u64,
    pub updated_at_unix_nanos: u64,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionFill {
    pub fill_id: String,
    pub order_id: String,
    #[serde(default)]
    pub plan_id: Option<String>,
    #[serde(default)]
    pub leg_id: Option<String>,
    pub intent_id: Option<String>,
    pub instrument_id: String,
    pub side: OrderSide,
    pub quantity_mantissa: i64,
    pub quantity_scale: u8,
    pub price_mantissa: i64,
    pub price_scale: u8,
    pub fee_mantissa: i64,
    pub fee_scale: u8,
    pub occurred_at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IntentStatus {
    Accepted,
    Planning,
    Planned,
    Executing,
    PartiallyFilled,
    CancelRequested,
    Satisfied,
    Rejected,
    Canceled,
    Expired,
    Failed,
    Compensating,
    ReconciliationRequired,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecuteStrategyIntent {
    pub intent_id: String,
    pub strategy_id: String,
    pub launch_id: String,
    pub instance_id: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub account_ids: Vec<String>,
    pub segment_key: String,
    pub target_quantity_mantissa: i64,
    pub quantity_scale: u8,
    pub limit_price_mantissa: Option<i64>,
    pub limit_price_scale: Option<u8>,
    pub source_snapshot_id: Option<String>,
    pub source_event_sequence: Option<u64>,
    pub reason: String,
    #[serde(default)]
    pub intent_type: crate::plan::IntentType,
    #[serde(default)]
    pub completion_policy: crate::plan::CompletionPolicy,
    #[serde(default)]
    pub failure_policy: crate::plan::FailurePolicy,
    #[serde(default)]
    pub legs: Vec<crate::plan::ExecutionIntentLeg>,
    #[serde(default)]
    pub deadline_unix_nanos: Option<u64>,
    #[serde(default)]
    pub min_edge_bps: Option<u32>,
    #[serde(default)]
    pub max_slippage_bps: Option<u32>,
    #[serde(default)]
    pub estimated_fee_bps: Option<u32>,
    #[serde(default)]
    pub hedge_policy: Option<crate::plan::HedgePolicy>,
    #[serde(default)]
    pub order_options: crate::plan::ExecutionOrderOptions,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct IntentState {
    pub intent: ExecuteStrategyIntent,
    pub status: IntentStatus,
    pub order_ids: Vec<String>,
    #[serde(default)]
    pub plan: Option<crate::plan::ExecutionPlan>,
    pub completed_quantity_mantissa: i64,
    pub updated_at_unix_nanos: u64,
    pub reason: String,
    #[serde(default)]
    pub dependency_watermarks: DependencyWatermarks,
    #[serde(default)]
    pub quote_version: u64,
    #[serde(default)]
    pub last_quote_refresh_unix_nanos: Option<u64>,
    #[serde(default)]
    pub compensation_attempts: u32,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct SnapshotWatermark {
    pub generation: u64,
    pub event_sequence: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct DependencyWatermarks {
    #[serde(default)]
    pub account: BTreeMap<String, SnapshotWatermark>,
    pub market: Option<SnapshotWatermark>,
    pub reference: Option<SnapshotWatermark>,
    pub risk: Option<SnapshotWatermark>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct IntentEvent {
    pub intent_id: String,
    pub event_sequence: u64,
    pub status: IntentStatus,
    pub order_ids: Vec<String>,
    pub completed_quantity_mantissa: i64,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
    #[serde(default)]
    pub dependency_watermarks: DependencyWatermarks,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub orders: Vec<ExecutionOrder>,
    pub events: Vec<ExecutionEvent>,
    pub fills: Vec<ExecutionFill>,
    pub intents: Vec<IntentState>,
    pub intent_events: Vec<IntentEvent>,
    pub intent_idempotency: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionEvent {
    pub order_id: String,
    #[serde(default)]
    pub intent_id: Option<String>,
    #[serde(default)]
    pub plan_id: Option<String>,
    #[serde(default)]
    pub leg_id: Option<String>,
    pub status: ExecutionOrderStatus,
    pub remote_order_id: Option<String>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
    pub fill_id: Option<String>,
    pub filled_quantity_mantissa: Option<i64>,
    pub filled_quantity_scale: Option<u8>,
}
