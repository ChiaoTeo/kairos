//! Intent, plan, hedge, split, and maker use cases.

use super::*;

mod lifecycle;
mod planning;
mod submission;

impl ExecutionApplication {
    pub fn intents(&self) -> Vec<IntentState> {
        self.actor.intents().cloned().collect()
    }

    pub fn intent(&self, intent_id: &str) -> Option<IntentState> {
        self.actor.intent(intent_id).cloned()
    }

    pub fn intent_events(&self, intent_id: Option<&str>) -> Vec<IntentEvent> {
        self.actor
            .intent_events()
            .iter()
            .filter(|event| intent_id.is_none_or(|id| event.intent_id == id))
            .cloned()
            .collect()
    }

    /// Paged event consumption for strategy/system recovery.  The returned
    /// sequence is strictly after `after_sequence`; callers can compare the
    /// first sequence with their watermark and fall back to the snapshot when
    /// a gap is detected.
    pub fn intent_events_after(
        &self,
        intent_id: Option<&str>,
        after_sequence: u64,
        limit: Option<usize>,
    ) -> Vec<IntentEvent> {
        let limit = limit.unwrap_or(usize::MAX);
        self.actor
            .intent_events()
            .iter()
            .filter(|event| {
                event.event_sequence.get() > after_sequence
                    && intent_id.is_none_or(|id| event.intent_id.as_str() == id)
            })
            .take(limit)
            .cloned()
            .collect()
    }

    /// Return hedge progress from actual fills.  This deliberately reports
    /// the required hedge quantity instead of submitting a synthetic order;
    /// the caller/scheduler can then apply exchange, price and risk checks before
    /// creating a compensating child order.
    pub fn hedge_requirement(
        &self,
        intent_id: &str,
    ) -> Result<Option<HedgeRequirement>, ExecutionError> {
        let Some(state) = self.actor.intent(intent_id) else {
            return Err(ExecutionError::Invalid("unknown intent".into()));
        };
        let Some(policy) = state.intent.hedge_policy.as_ref() else {
            return Ok(None);
        };
        let Some(plan) = state.plan.as_ref() else {
            return Ok(None);
        };
        let filled_for = |leg_id: &str| -> Result<Quantity, ExecutionError> {
            let Some(leg) = plan.legs.iter().find(|leg| leg.leg_id == leg_id) else {
                return Err(ExecutionError::Invalid(format!(
                    "hedge leg is missing from execution plan: {leg_id}"
                )));
            };
            leg.order_ids
                .iter()
                .try_fold(Quantity::ZERO, |total, order_id| {
                    total
                        .checked_add(
                            self.actor
                                .order_map()
                                .get(order_id.as_str())
                                .map(|order| order.filled_quantity)
                                .unwrap_or(Quantity::ZERO),
                        )
                        .map_err(|_| ExecutionError::Invalid("hedge fill quantity overflow".into()))
                })
        };
        let leader = filled_for(&policy.leader_leg_id)?;
        let hedge = filled_for(&policy.hedge_leg_id)?;
        let required = policy
            .required_hedge_quantity(leader, Quantity::ZERO)
            .map_err(ExecutionError::Invalid)?;
        let unhedged = if hedge >= required {
            Quantity::ZERO
        } else {
            required
                .checked_sub(hedge)
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?
        };
        Ok(Some(HedgeRequirement {
            intent_id: typed_intent_id(intent_id),
            leader_leg_id: policy.leader_leg_id.clone(),
            hedge_leg_id: policy.hedge_leg_id.clone(),
            leader_filled_quantity: leader,
            hedge_filled_quantity: hedge,
            required_hedge_quantity: required,
            unhedged_quantity: unhedged,
            max_unhedged_quantity: policy.max_unhedged_quantity,
            within_tolerance: unhedged <= policy.max_unhedged_quantity,
            compensation_attempts: state.compensation_attempts,
            max_compensation_attempts: policy.max_compensation_attempts,
        }))
    }

    /// Submit a compensating hedge only for the quantity that is still
    /// missing after considering both fills and already-active hedge orders.
    /// This is deliberately fill-driven: a large original hedge target never
    /// causes an additional order unless the leader leg actually fills.
    pub(super) fn maybe_submit_compensating_hedge(
        &mut self,
        intent_id: &str,
    ) -> Result<(), ExecutionError> {
        let Some(state) = self.actor.intent(intent_id).cloned() else {
            return Ok(());
        };
        let Some(policy) = state.intent.hedge_policy.clone() else {
            return Ok(());
        };
        let Some(plan) = state.plan.clone() else {
            return Ok(());
        };
        let requirement = self
            .hedge_requirement(intent_id)?
            .ok_or_else(|| ExecutionError::Invalid("hedge policy could not be evaluated".into()))?;
        let Some(hedge_leg) = plan
            .legs
            .iter()
            .find(|leg| leg.leg_id == policy.hedge_leg_id)
        else {
            return Err(ExecutionError::Invalid("hedge leg is missing".into()));
        };
        let active_quantity = hedge_leg
            .order_ids
            .iter()
            .filter_map(|order_id| self.actor.order_map().get(order_id.as_str()))
            .filter(|order| !order.status.terminal())
            .try_fold(0_i64, |total, order| {
                total.checked_add(
                    order
                        .quantity
                        .mantissa()
                        .saturating_sub(order.filled_quantity.mantissa()),
                )
            })
            .ok_or_else(|| ExecutionError::Invalid("active hedge quantity overflow".into()))?;
        let missing = requirement
            .required_hedge_quantity
            .mantissa()
            .saturating_sub(requirement.hedge_filled_quantity.mantissa())
            .saturating_sub(active_quantity);
        if missing <= requirement.max_unhedged_quantity.mantissa() {
            return Ok(());
        }
        if state.compensation_attempts >= policy.max_compensation_attempts {
            self.commit_intent(IntentEvent {
                intent_id: typed_intent_id(intent_id),
                event_sequence: 0.into(),
                status: IntentStatus::ReconciliationRequired,
                order_ids: Vec::new(),
                completed_quantity: state.completed_quantity,
                occurred_at_unix_nanos: now_nanos().into(),
                reason: format!(
                    "compensation breaker opened after {} attempts; unhedged={missing}",
                    state.compensation_attempts
                ),
                dependency_watermarks: state.dependency_watermarks,
            })?;
            return Ok(());
        }
        if !policy.compensate_on_failure {
            let current = self
                .actor
                .intent(intent_id)
                .cloned()
                .ok_or_else(|| ExecutionError::Invalid("hedge intent disappeared".into()))?;
            self.commit_intent(IntentEvent {
                intent_id: typed_intent_id(intent_id),
                event_sequence: 0.into(),
                status: IntentStatus::ReconciliationRequired,
                order_ids: Vec::new(),
                completed_quantity: current.completed_quantity,
                occurred_at_unix_nanos: now_nanos().into(),
                reason: "hedge exposure exceeds tolerance and compensation is disabled".into(),
                dependency_watermarks: current.dependency_watermarks,
            })?;
            return Ok(());
        }
        let template_id = hedge_leg
            .order_ids
            .first()
            .ok_or_else(|| ExecutionError::Invalid("hedge leg has no order template".into()))?;
        let template = self
            .actor
            .order_map()
            .get(template_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("hedge order template is missing".into()))?;
        let order_id = format!(
            "{}:compensate:{}",
            intent_id,
            requirement.required_hedge_quantity.mantissa()
        );
        if self.actor.order_map().contains_key(order_id.as_str()) {
            return Ok(());
        }
        let options = state
            .intent
            .legs
            .iter()
            .find(|leg| leg.leg_id == policy.hedge_leg_id)
            .map(|leg| leg.options.clone())
            .unwrap_or_else(|| state.intent.order_options.clone());
        let request = SubmitOrder {
            order_id: OrderId::new(order_id).expect("validated compensating order ID"),
            intent_id: Some(IntentId::new(intent_id).expect("validated intent ID")),
            strategy_id: Some(typed_strategy_id(state.intent.strategy_id.clone())),
            account_id: template.account_id.clone(),
            segment_key: template.segment_key.clone(),
            instrument_id: template.instrument_id.clone(),
            market_id: template.market_id.clone(),
            execution_route_id: template.execution_route_id.clone(),
            side: template.side,
            order_type: template.order_type,
            quantity: Quantity::new(missing, template.quantity.scale())
                .expect("validated compensating quantity"),
            limit_price: template.limit_price,
            options,
            submitted_at_unix_nanos: Some(template.submitted_at_unix_nanos),
        };
        self.actor.increment_compensation_attempts(intent_id);
        match self.submit(request) {
            Ok(order) => {
                self.attach_plan_order(intent_id, &policy.hedge_leg_id, &order.order_id)?;
                let state =
                    self.actor.intent(intent_id).cloned().ok_or_else(|| {
                        ExecutionError::Invalid("hedge intent disappeared".into())
                    })?;
                self.commit_intent(IntentEvent {
                    intent_id: typed_intent_id(intent_id),
                    event_sequence: 0.into(),
                    status: IntentStatus::Compensating,
                    order_ids: vec![order.order_id.clone()],
                    completed_quantity: state.completed_quantity,
                    occurred_at_unix_nanos: now_nanos().into(),
                    reason: "leader fill exceeded active hedge quantity".into(),
                    dependency_watermarks: state.dependency_watermarks,
                })?;
            }
            Err(error) => {
                self.commit_intent(IntentEvent {
                    intent_id: typed_intent_id(intent_id),
                    event_sequence: 0.into(),
                    status: IntentStatus::ReconciliationRequired,
                    order_ids: Vec::new(),
                    completed_quantity: state.completed_quantity,
                    occurred_at_unix_nanos: now_nanos().into(),
                    reason: format!("compensating hedge failed: {error}"),
                    dependency_watermarks: state.dependency_watermarks,
                })?;
            }
        }
        Ok(())
    }

    pub fn drain_intent_events(&mut self) -> Vec<IntentEvent> {
        self.actor.drain_intent_events()
    }
}
