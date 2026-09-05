//! Split, maker, and hedge policy values.

use kairos_primitives::execution::ExecutionRouteId;

use super::*;

/// Controls how one logical leg is materialized into exchange child orders.
/// Quantities use the leg's quantity scale; the planner never rounds away
/// quantity and always preserves the exact requested total.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SplitOrderPolicy {
    #[serde(default)]
    pub max_child_quantity: Option<Quantity>,
    #[serde(default)]
    pub child_count: Option<u32>,
    #[serde(default)]
    pub min_child_quantity: Option<Quantity>,
}

impl SplitOrderPolicy {
    pub fn validate(&self) -> Result<(), IntentError> {
        if self.max_child_quantity.is_some_and(|value| value.is_zero())
            || self.min_child_quantity.is_some_and(|value| value.is_zero())
            || self.child_count.is_some_and(|value| value == 0)
        {
            return Err(IntentError::SplitPolicyNonPositive);
        }
        if let (Some(minimum), Some(maximum)) = (self.min_child_quantity, self.max_child_quantity) {
            if minimum > maximum {
                return Err(IntentError::SplitMinimumExceedsMaximum);
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TwapPolicy {
    pub slice_count: u32,
    pub slice_interval: DurationNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PassiveLimitPolicy {
    /// Minimum business-time interval between accepted quote revisions.
    pub reprice_interval: DurationNanos,
    /// Maximum age of the market observation that supplies replacement prices.
    pub max_quote_age: DurationNanos,
}

impl PassiveLimitPolicy {
    pub fn validate(&self) -> Result<(), IntentError> {
        if self.reprice_interval.get() == 0 || self.max_quote_age.get() == 0 {
            return Err(IntentError::InvalidPassiveLimitPolicy);
        }
        Ok(())
    }
}

impl TwapPolicy {
    pub fn validate(&self) -> Result<(), IntentError> {
        if self.slice_count < 2 || self.slice_interval.get() == 0 {
            return Err(IntentError::InvalidTwapPolicy);
        }
        Ok(())
    }
}

/// Admission-only inventory and quote-freshness guardrails for maker orders.
/// Algorithm cadence belongs exclusively to the selected algorithm spec.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MakerExecutionPolicy {
    #[serde(default)]
    pub max_inventory_abs: Option<SignedQuantity>,
    #[serde(default)]
    pub target_inventory: Option<SignedQuantity>,
    #[serde(default)]
    pub max_quote_age: Option<DurationNanos>,
}

impl MakerExecutionPolicy {
    pub fn validate(&self) -> Result<(), IntentError> {
        if self
            .max_inventory_abs
            .is_some_and(|value| value.is_negative())
            || self.max_quote_age.is_some_and(|value| value.get() == 0)
        {
            return Err(IntentError::InvalidMakerPolicy);
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
    pub fn validate(&self) -> Result<(), IntentError> {
        if self.leader_leg_id == self.hedge_leg_id
            || self.max_compensation_attempts == 0
            || self
                .max_unhedged_duration
                .is_some_and(|duration| duration.get() == 0)
        {
            return Err(IntentError::InvalidHedgePolicy);
        }
        let mut routes = std::collections::BTreeSet::new();
        for route_id in &self.fallback_execution_route_ids {
            if !routes.insert(route_id) {
                return Err(IntentError::DuplicateHedgeRoute {
                    route_id: route_id.clone(),
                });
            }
        }
        Ok(())
    }

    pub fn required_hedge_quantity(
        &self,
        leader_filled: Quantity,
        hedge_filled: Quantity,
    ) -> Result<Quantity, IntentError> {
        let required = self
            .ratio
            .apply_to_nonnegative(leader_filled.mantissa())
            .map_err(|source| IntentError::Arithmetic {
                operation: "hedge ratio application",
                source,
            })?;
        let required = self
            .contract_multiplier
            .apply_to_nonnegative(required)
            .map_err(|source| IntentError::Arithmetic {
                operation: "hedge contract multiplier application",
                source,
            })?;
        let required = Quantity::new(required, leader_filled.scale()).map_err(|source| {
            IntentError::Arithmetic {
                operation: "hedge quantity construction",
                source,
            }
        })?;
        if hedge_filled >= required {
            Ok(Quantity::ZERO)
        } else {
            required
                .checked_sub(hedge_filled)
                .map_err(|source| IntentError::Arithmetic {
                    operation: "filled hedge subtraction",
                    source,
                })
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(
    tag = "type",
    content = "policy",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum ExecutionAlgorithmPolicy {
    Immediate,
    Twap(TwapPolicy),
    PassiveLimit(PassiveLimitPolicy),
    MakerTakerHedge(HedgePolicy),
}

impl ExecutionAlgorithmPolicy {
    pub fn validate(&self) -> Result<(), IntentError> {
        match self {
            Self::Immediate => Ok(()),
            Self::Twap(policy) => policy.validate(),
            Self::PassiveLimit(policy) => policy.validate(),
            Self::MakerTakerHedge(policy) => policy.validate(),
        }
    }

    pub fn hedge_policy(&self) -> Option<&HedgePolicy> {
        match self {
            Self::MakerTakerHedge(policy) => Some(policy),
            Self::Immediate | Self::Twap(_) | Self::PassiveLimit(_) => None,
        }
    }
}
