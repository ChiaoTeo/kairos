use serde::{Deserialize, Serialize};

use crate::model::{Decimal, ExecutionOrderStatus, OrderSide};

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct SplitOrderPolicy {
    pub max_child_quantity: Option<Decimal>,
    pub child_count: Option<u32>,
    pub min_child_quantity: Option<Decimal>,
    pub interval: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct MakerExecutionPolicy {
    pub min_interval: Option<u64>,
    pub max_orders_per_window: Option<u32>,
    pub window: Option<u64>,
    pub max_inventory_abs: Option<Decimal>,
    pub target_inventory: Option<Decimal>,
    pub max_quote_age: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct ExecutionOrderOptions {
    pub time_in_force: Option<String>,
    pub reduce_only: Option<bool>,
    pub post_only: Option<bool>,
    pub position_side: Option<String>,
    pub quote_asset: Option<String>,
    pub wallet_type: Option<String>,
    pub trading_session: Option<String>,
    pub tokenize: Option<bool>,
    #[serde(default)]
    pub split: Option<SplitOrderPolicy>,
    #[serde(default)]
    pub maker: Option<MakerExecutionPolicy>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct HedgePolicy {
    pub leader_leg_id: String,
    pub hedge_leg_id: String,
    pub ratio: Ratio,
    #[serde(default = "one_ratio")]
    pub contract_multiplier: Ratio,
    pub max_unhedged_quantity: Decimal,
    pub compensate_on_failure: bool,
    #[serde(default = "default_compensation_attempts")]
    pub max_compensation_attempts: u32,
}

fn default_compensation_attempts() -> u32 {
    3
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Ratio {
    pub numerator: u64,
    pub denominator: u64,
}

fn one_ratio() -> Ratio {
    Ratio {
        numerator: 1,
        denominator: 1,
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionIntentLeg {
    pub leg_id: String,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub side: OrderSide,
    pub quantity: Decimal,
    pub limit_price: Option<Decimal>,
    #[serde(default)]
    pub target_position: bool,
    #[serde(default)]
    pub options: ExecutionOrderOptions,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PairArbitrageIntent {
    pub intent_id: String,
    pub strategy_id: String,
    pub launch_id: String,
    pub instance_id: String,
    pub account_id: String,
    pub segment_key: String,
    pub first: PairArbitrageLeg,
    pub second: PairArbitrageLeg,
    pub completion_policy: CompletionPolicy,
    pub failure_policy: FailurePolicy,
    pub reason: String,
    #[serde(default)]
    pub hedge_policy: Option<HedgePolicy>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PairArbitrageLeg {
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub side: OrderSide,
    pub quantity: Decimal,
    pub limit_price: Option<Decimal>,
    #[serde(default)]
    pub options: ExecutionOrderOptions,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PortfolioRebalanceTarget {
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub target_quantity: Decimal,
    pub side: OrderSide,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PortfolioRebalanceIntent {
    pub intent_id: String,
    pub strategy_id: String,
    pub launch_id: String,
    pub instance_id: String,
    pub account_id: String,
    pub segment_key: String,
    pub targets: Vec<PortfolioRebalanceTarget>,
    pub completion_policy: CompletionPolicy,
    pub failure_policy: FailurePolicy,
    pub reason: String,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentType {
    SingleOrder,
    #[default]
    TargetPosition,
    PairArbitrage,
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

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentLifecycle {
    Accepted,
    Planning,
    Planned,
    Executing,
    PartiallyFilled,
    Satisfied,
    Rejected,
    CancelRequested,
    Canceled,
    Expired,
    Failed,
    Compensating,
    ReconciliationRequired,
}

impl IntentLifecycle {
    pub fn terminal(self) -> bool {
        matches!(
            self,
            Self::Satisfied
                | Self::Rejected
                | Self::Canceled
                | Self::Expired
                | Self::Failed
                | Self::ReconciliationRequired
        )
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum LegLifecycle {
    Pending,
    Ready,
    Executing,
    PartiallyFilled,
    Satisfied,
    Canceled,
    Failed,
    Compensating,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionLeg {
    pub leg_id: String,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub side: OrderSide,
    pub target_quantity: Decimal,
    pub order_ids: Vec<String>,
    pub lifecycle: LegLifecycle,
    pub completed_quantity: Decimal,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionPlan {
    pub plan_id: String,
    pub intent_id: String,
    pub intent_type: IntentType,
    pub legs: Vec<ExecutionLeg>,
    pub completion_policy: CompletionPolicy,
    pub failure_policy: FailurePolicy,
}

impl ExecutionPlan {
    pub fn lifecycle(
        &self,
        orders: impl IntoIterator<Item = ExecutionOrderStatus>,
    ) -> IntentLifecycle {
        let statuses: Vec<_> = orders.into_iter().collect();
        if statuses.is_empty() {
            return IntentLifecycle::Planned;
        }
        if statuses
            .iter()
            .all(|status| *status == ExecutionOrderStatus::Filled)
        {
            IntentLifecycle::Satisfied
        } else if statuses.iter().any(|status| {
            matches!(
                status,
                ExecutionOrderStatus::PartiallyFilled | ExecutionOrderStatus::Filled
            )
        }) {
            IntentLifecycle::PartiallyFilled
        } else if statuses.iter().any(|status| !status.terminal()) {
            IntentLifecycle::Executing
        } else {
            IntentLifecycle::Failed
        }
    }
}
