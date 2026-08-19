//! Deterministic plan construction and quantity splitting.

use super::*;

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
                },
                _ => IntentLifecycle::Failed,
            }
        } else {
            IntentLifecycle::Executing
        }
    }
}
