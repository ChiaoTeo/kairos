//! Split, maker, and hedge policy values.

use super::*;
use kairos_primitives::execution::ExecutionRouteId;

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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TwapPolicy {
    pub slice_count: u32,
    pub slice_interval: DurationNanos,
}

impl TwapPolicy {
    pub fn validate(&self) -> Result<(), String> {
        if self.slice_count < 2 || self.slice_interval.get() == 0 {
            return Err("TWAP requires at least two slices and a positive interval".into());
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
    /// Maximum business-time duration for any non-zero filled exposure,
    /// including a tail that remains inside the quantity tolerance.
    #[serde(default)]
    pub max_unhedged_duration: Option<DurationNanos>,
    /// Ordered alternative routes for a taker hedge proven not sent or
    /// explicitly rejected. They are never used after an indeterminate send.
    #[serde(default)]
    pub fallback_execution_route_ids: Vec<ExecutionRouteId>,
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
        if self.leader_leg_id == self.hedge_leg_id
            || self.max_compensation_attempts == 0
            || self
                .max_unhedged_duration
                .is_some_and(|duration| duration.get() == 0)
        {
            return Err("hedge policy is invalid".into());
        }
        let mut routes = std::collections::BTreeSet::new();
        if self
            .fallback_execution_route_ids
            .iter()
            .any(|route_id| !routes.insert(route_id))
        {
            return Err("hedge fallback execution routes must be unique".into());
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", content = "policy", rename_all = "snake_case")]
pub enum ExecutionAlgorithmPolicy {
    Immediate,
    Twap(TwapPolicy),
    MakerTakerHedge(HedgePolicy),
}

impl ExecutionAlgorithmPolicy {
    pub fn validate(&self) -> Result<(), String> {
        match self {
            Self::Immediate => Ok(()),
            Self::Twap(policy) => policy.validate(),
            Self::MakerTakerHedge(policy) => policy.validate(),
        }
    }

    pub fn hedge_policy(&self) -> Option<&HedgePolicy> {
        match self {
            Self::MakerTakerHedge(policy) => Some(policy),
            Self::Immediate | Self::Twap(_) => None,
        }
    }
}
