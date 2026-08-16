use kairos_primitives::{
    AccountId, DurationNanos, InstrumentId, IntentId, LegId, MarketId, OrderId, PlanId, Quantity,
    Ratio, SegmentKey, SignedQuantity,
};
use serde::{Deserialize, Serialize};

use super::{ExecutionOrderStatus, OrderSide};

/// Controls how one logical leg is materialized into exchange child orders.
/// Quantities use the leg's quantity scale; the planner never rounds away
/// quantity and always preserves the exact requested total.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SplitOrderPolicy {
    #[serde(default)]
    pub max_child_quantity: Option<Quantity>,
    #[serde(default)]
    pub child_count: Option<u32>,
    #[serde(default)]
    pub min_child_quantity: Option<Quantity>,
    #[serde(default)]
    pub interval: Option<DurationNanos>,
}

impl SplitOrderPolicy {
    pub fn validate(&self) -> Result<(), String> {
        if self.max_child_quantity.is_some_and(|value| value.is_zero())
            || self.min_child_quantity.is_some_and(|value| value.is_zero())
            || self.child_count.is_some_and(|value| value == 0)
        {
            return Err("split policy quantities and child_count must be positive".into());
        }
        if let (Some(minimum), Some(maximum)) = (self.min_child_quantity, self.max_child_quantity) {
            if minimum > maximum {
                return Err("split policy minimum exceeds maximum child quantity".into());
            }
        }
        Ok(())
    }
}

/// A maker quote is allowed to be throttled by both order cadence and
/// inventory.  This is a guardrail consumed by the execution scheduler; it
/// is not a signal-generation policy.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MakerExecutionPolicy {
    #[serde(default)]
    pub min_interval: Option<DurationNanos>,
    #[serde(default)]
    pub max_orders_per_window: Option<u32>,
    #[serde(default)]
    pub window: Option<DurationNanos>,
    #[serde(default)]
    pub max_inventory_abs: Option<SignedQuantity>,
    #[serde(default)]
    pub target_inventory: Option<SignedQuantity>,
    #[serde(default)]
    pub max_quote_age: Option<DurationNanos>,
}

impl MakerExecutionPolicy {
    pub fn validate(&self) -> Result<(), String> {
        if self.min_interval.is_some_and(|value| value.get() == 0)
            || self.max_orders_per_window.is_some_and(|value| value == 0)
            || self.window.is_some_and(|value| value.get() == 0)
            || self
                .max_inventory_abs
                .is_some_and(|value| value.is_negative())
            || self.max_quote_age.is_some_and(|value| value.get() == 0)
        {
            return Err("maker execution policy contains an invalid limit".into());
        }
        Ok(())
    }
}

/// Pair legs are coupled by actual fills, not by their original requested
/// quantities.  `numerator / denominator` is the hedge quantity per unit of
/// leader fill.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct HedgePolicy {
    pub leader_leg_id: LegId,
    pub hedge_leg_id: LegId,
    pub ratio: Ratio,
    /// Additional normalized quantity conversion, e.g. leader contract size
    /// divided by hedge contract size for cross-exchange derivatives.
    #[serde(default = "one_ratio")]
    pub contract_multiplier: Ratio,
    #[serde(default)]
    pub max_unhedged_quantity: Quantity,
    #[serde(default)]
    pub compensate_on_failure: bool,
    /// Maximum number of compensating submissions before Execution stops
    /// retrying and requires reconciliation/manual intervention.
    #[serde(default = "default_compensation_attempts")]
    pub max_compensation_attempts: u32,
}

fn one_ratio() -> Ratio {
    Ratio::new(1, 1).expect("one is a valid ratio")
}

fn default_compensation_attempts() -> u32 {
    3
}

impl HedgePolicy {
    pub fn validate(&self) -> Result<(), String> {
        if self.leader_leg_id == self.hedge_leg_id || self.max_compensation_attempts == 0 {
            return Err("hedge policy is invalid".into());
        }
        Ok(())
    }

    pub fn required_hedge_quantity(
        &self,
        leader_filled: Quantity,
        hedge_filled: Quantity,
    ) -> Result<Quantity, String> {
        let required = self
            .ratio
            .apply_to_nonnegative(leader_filled.mantissa())
            .map_err(|error| error.to_string())?;
        let required = self
            .contract_multiplier
            .apply_to_nonnegative(required)
            .map_err(|error| error.to_string())?;
        let required =
            Quantity::new(required, leader_filled.scale()).map_err(|error| error.to_string())?;
        if hedge_filled >= required {
            Ok(Quantity::ZERO)
        } else {
            required
                .checked_sub(hedge_filled)
                .map_err(|error| error.to_string())
        }
    }
}

/// Split an exact quantity deterministically.  The returned chunks sum to
/// `total`; this makes retries and recovery stable because the child IDs can
/// be derived from their ordinal.
pub fn split_quantity(total: Quantity, policy: &SplitOrderPolicy) -> Result<Vec<Quantity>, String> {
    if total.is_zero() {
        return Err("split quantity must be positive".into());
    }
    policy.validate()?;
    let mut scale = total
        .scale()
        .max(policy.max_child_quantity.map_or(0, Quantity::scale))
        .max(policy.min_child_quantity.map_or(0, Quantity::scale));
    let mut total_mantissa = rescale_quantity(total, scale)?;
    let mut count = policy.child_count.unwrap_or(1) as i64;
    if let Some(maximum) = policy.max_child_quantity {
        let maximum = rescale_quantity(maximum, scale)?;
        count = count.max((total_mantissa + maximum - 1) / maximum);
    }
    while count > total_mantissa && scale < kairos_primitives::MAX_DECIMAL_SCALE {
        scale += 1;
        total_mantissa = total_mantissa
            .checked_mul(10)
            .ok_or_else(|| "split quantity overflows".to_string())?;
    }
    if count <= 0 || count > total_mantissa {
        return Err("split policy produces an invalid child count".into());
    }
    let base = total_mantissa / count;
    let remainder = total_mantissa % count;
    if let Some(minimum) = policy.min_child_quantity {
        if base < rescale_quantity(minimum, scale)? {
            return Err("split policy minimum child quantity cannot be satisfied".into());
        }
    }
    let mut chunks = Vec::with_capacity(count as usize);
    for index in 0..count {
        chunks.push(
            Quantity::new(base + i64::from(index < remainder), scale)
                .expect("split quantity is non-negative"),
        );
    }
    Ok(chunks)
}

fn rescale_quantity(value: Quantity, scale: u8) -> Result<i64, String> {
    let factor = 10_i64
        .checked_pow(u32::from(scale.saturating_sub(value.scale())))
        .ok_or_else(|| "split quantity scale overflows".to_string())?;
    value
        .mantissa()
        .checked_mul(factor)
        .ok_or_else(|| "split quantity overflows".to_string())
}

/// Business-level intent kinds.  An intent describes an outcome; exchange orders
/// remain an Execution implementation detail.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentType {
    SingleOrder,
    #[default]
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

    pub fn can_transition_to(self, next: Self) -> bool {
        use IntentLifecycle::*;
        matches!(
            (self, next),
            (Accepted, Planning)
                | (Accepted, Rejected)
                | (Planning, Planned)
                | (Planning, Rejected)
                | (Planned, Executing)
                | (Planned, Rejected)
                | (Executing, PartiallyFilled)
                | (Executing, Satisfied)
                | (Executing, CancelRequested)
                | (Executing, Expired)
                | (Executing, Failed)
                | (Executing, Compensating)
                | (PartiallyFilled, Executing)
                | (PartiallyFilled, Satisfied)
                | (PartiallyFilled, CancelRequested)
                | (PartiallyFilled, Expired)
                | (PartiallyFilled, Failed)
                | (PartiallyFilled, Compensating)
                | (CancelRequested, Canceled)
                | (CancelRequested, PartiallyFilled)
                | (CancelRequested, Compensating)
                | (Compensating, Satisfied)
                | (Compensating, Failed)
                | (Compensating, ReconciliationRequired)
                | (_, ReconciliationRequired)
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

impl LegLifecycle {
    pub fn terminal(self) -> bool {
        matches!(self, Self::Satisfied | Self::Canceled | Self::Failed)
    }

    pub fn can_transition_to(self, next: Self) -> bool {
        use LegLifecycle::*;
        matches!(
            (self, next),
            (Pending, Ready)
                | (Ready, Executing)
                | (Executing, PartiallyFilled)
                | (Executing, Satisfied)
                | (Executing, Canceled)
                | (Executing, Failed)
                | (Executing, Compensating)
                | (PartiallyFilled, Executing)
                | (PartiallyFilled, Satisfied)
                | (PartiallyFilled, Canceled)
                | (PartiallyFilled, Failed)
                | (PartiallyFilled, Compensating)
                | (Compensating, Satisfied)
                | (Compensating, Failed)
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionLeg {
    pub leg_id: LegId,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    pub side: OrderSide,
    pub target_quantity: Quantity,
    pub order_ids: Vec<OrderId>,
    pub lifecycle: LegLifecycle,
    pub completed_quantity: Quantity,
    pub reason: String,
}

impl ExecutionLeg {
    pub fn new(
        leg_id: impl Into<String>,
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
        instrument_id: impl Into<String>,
        side: OrderSide,
        target_quantity: Quantity,
    ) -> Result<Self, String> {
        let value = Self {
            leg_id: LegId::new(leg_id.into()).map_err(|error| error.to_string())?,
            account_id: AccountId::new(account_id.into()).map_err(|error| error.to_string())?,
            segment_key: SegmentKey::new(segment_key.into()).map_err(|error| error.to_string())?,
            instrument_id: InstrumentId::new(instrument_id.into())
                .map_err(|error| format!("invalid instrument_id: {error}"))?,
            market_id: None,
            side,
            target_quantity,
            order_ids: Vec::new(),
            lifecycle: LegLifecycle::Pending,
            completed_quantity: Quantity::ZERO,
            reason: String::new(),
        };
        Ok(value)
    }

    pub fn transition(
        &mut self,
        next: LegLifecycle,
        reason: impl Into<String>,
    ) -> Result<(), String> {
        if self.lifecycle != next && !self.lifecycle.can_transition_to(next) {
            return Err(format!(
                "invalid execution leg transition: {:?} -> {:?}",
                self.lifecycle, next
            ));
        }
        self.lifecycle = next;
        self.reason = reason.into();
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionPlan {
    pub plan_id: PlanId,
    pub intent_id: IntentId,
    pub intent_type: IntentType,
    pub legs: Vec<ExecutionLeg>,
    pub completion_policy: CompletionPolicy,
    pub failure_policy: FailurePolicy,
}

impl ExecutionPlan {
    pub fn new(
        plan_id: impl Into<String>,
        intent_id: impl Into<String>,
        intent_type: IntentType,
        legs: Vec<ExecutionLeg>,
        completion_policy: CompletionPolicy,
        failure_policy: FailurePolicy,
    ) -> Result<Self, String> {
        let value = Self {
            plan_id: PlanId::new(plan_id.into()).map_err(|error| error.to_string())?,
            intent_id: IntentId::new(intent_id.into()).map_err(|error| error.to_string())?,
            intent_type,
            legs,
            completion_policy,
            failure_policy,
        };
        if value.legs.is_empty() {
            return Err("execution plan requires at least one leg".into());
        }
        let mut ids = std::collections::BTreeSet::new();
        for leg in &value.legs {
            if !ids.insert(leg.leg_id.clone()) {
                return Err(format!("duplicate execution leg: {}", leg.leg_id));
            }
        }
        Ok(value)
    }

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
        } else if statuses.contains(&ExecutionOrderStatus::Unknown) {
            IntentLifecycle::ReconciliationRequired
        } else if statuses.iter().any(|status| {
            matches!(
                status,
                ExecutionOrderStatus::PartiallyFilled | ExecutionOrderStatus::Filled
            )
        }) {
            IntentLifecycle::PartiallyFilled
        } else if statuses.iter().any(|status| !status.terminal()) {
            IntentLifecycle::Executing
        } else if statuses.iter().all(|status| status.terminal()) {
            match self.completion_policy {
                CompletionPolicy::BestEffort
                    if statuses.contains(&ExecutionOrderStatus::Filled) =>
                {
                    IntentLifecycle::Satisfied
                }
                _ => IntentLifecycle::Failed,
            }
        } else {
            IntentLifecycle::Executing
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_invalid_state_transition() {
        assert!(!IntentLifecycle::Accepted.can_transition_to(IntentLifecycle::Satisfied));
        assert!(IntentLifecycle::Accepted.can_transition_to(IntentLifecycle::Planning));
    }

    #[test]
    fn plan_requires_unique_legs() {
        let first = ExecutionLeg::new(
            "leg",
            "account",
            "spot",
            "BTCUSDT",
            OrderSide::Buy,
            Quantity::new(1, 0).unwrap(),
        )
        .unwrap();
        let second = first.clone();
        assert!(ExecutionPlan::new(
            "plan",
            "intent",
            IntentType::PairArbitrage,
            vec![first, second],
            CompletionPolicy::AllLegsSatisfied,
            FailurePolicy::CancelRemaining,
        )
        .is_err());
    }

    #[test]
    fn plan_classifies_orders_by_progress() {
        let leg = ExecutionLeg::new(
            "leg",
            "account",
            "spot",
            "BTCUSDT",
            OrderSide::Buy,
            Quantity::new(1, 0).unwrap(),
        )
        .unwrap();
        let plan = ExecutionPlan::new(
            "plan",
            "intent",
            IntentType::SingleOrder,
            vec![leg],
            CompletionPolicy::AllLegsSatisfied,
            FailurePolicy::CancelRemaining,
        )
        .unwrap();
        assert_eq!(
            plan.lifecycle([ExecutionOrderStatus::Accepted]),
            IntentLifecycle::Executing
        );
        assert_eq!(
            plan.lifecycle([ExecutionOrderStatus::PartiallyFilled]),
            IntentLifecycle::PartiallyFilled
        );
        assert_eq!(
            plan.lifecycle([ExecutionOrderStatus::Filled]),
            IntentLifecycle::Satisfied
        );
    }

    #[test]
    fn split_quantity_preserves_total_and_is_deterministic() {
        let chunks = split_quantity(
            Quantity::new(10, 0).unwrap(),
            &SplitOrderPolicy {
                max_child_quantity: None,
                child_count: Some(3),
                min_child_quantity: Some(Quantity::new(3, 0).unwrap()),
                interval: Some(DurationNanos::new(100_000_000)),
            },
        )
        .unwrap();
        assert_eq!(
            chunks,
            vec![
                Quantity::new(4, 0).unwrap(),
                Quantity::new(3, 0).unwrap(),
                Quantity::new(3, 0).unwrap()
            ]
        );
        assert_eq!(chunks.iter().map(|value| value.mantissa()).sum::<i64>(), 10);
    }

    #[test]
    fn split_quantity_can_increase_precision_after_canonicalization() {
        let chunks = split_quantity(
            Quantity::new(1, 0).unwrap(),
            &SplitOrderPolicy {
                max_child_quantity: None,
                child_count: Some(2),
                min_child_quantity: None,
                interval: None,
            },
        )
        .unwrap();

        assert_eq!(
            chunks,
            vec![Quantity::new(5, 1).unwrap(), Quantity::new(5, 1).unwrap()]
        );
    }

    #[test]
    fn hedge_requirement_uses_actual_leader_fills() {
        let policy = HedgePolicy {
            leader_leg_id: LegId::new("leader").unwrap(),
            hedge_leg_id: LegId::new("hedge").unwrap(),
            ratio: Ratio::new(2, 1).unwrap(),
            contract_multiplier: Ratio::new(1, 1).unwrap(),
            max_unhedged_quantity: Quantity::new(1, 0).unwrap(),
            compensate_on_failure: true,
            max_compensation_attempts: 3,
        };
        assert_eq!(
            policy
                .required_hedge_quantity(Quantity::new(3, 0).unwrap(), Quantity::new(4, 0).unwrap())
                .unwrap(),
            Quantity::new(2, 0).unwrap()
        );
        assert_eq!(
            policy
                .required_hedge_quantity(Quantity::new(3, 0).unwrap(), Quantity::new(6, 0).unwrap())
                .unwrap(),
            Quantity::ZERO
        );
    }
}
