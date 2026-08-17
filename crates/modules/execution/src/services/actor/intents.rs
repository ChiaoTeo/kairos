use super::*;

impl ExecutionActor {
    pub(crate) fn contains_intent(&self, intent_id: &str) -> bool {
        self.intents.contains_key(intent_id)
    }

    pub(crate) fn restore_intents(
        &mut self,
        intents: Vec<IntentState>,
        idempotency: BTreeMap<String, String>,
    ) {
        self.intents = intents
            .into_iter()
            .map(|state| (state.intent.intent_id.to_string(), state))
            .collect();
        self.intent_idempotency = idempotency;
    }

    pub(crate) fn insert_intent(&mut self, state: IntentState) {
        self.intents
            .insert(state.intent.intent_id.to_string(), state);
    }

    pub(crate) fn intent_for_idempotency_key(
        &self,
        key: &str,
    ) -> Result<Option<&IntentState>, String> {
        self.intent_idempotency
            .get(key)
            .map(|intent_id| {
                self.intents
                    .get(intent_id)
                    .ok_or_else(|| "idempotency record references missing intent".to_string())
            })
            .transpose()
    }

    pub(crate) fn record_intent_idempotency(&mut self, key: String, intent_id: String) {
        self.intent_idempotency.insert(key, intent_id);
    }

    pub(crate) fn intent_idempotency(&self) -> &BTreeMap<String, String> {
        &self.intent_idempotency
    }

    pub(crate) fn increment_compensation_attempts(&mut self, intent_id: &str) {
        if let Some(state) = self.intents.get_mut(intent_id) {
            state.compensation_attempts = state.compensation_attempts.saturating_add(1);
        }
    }

    pub(crate) fn remove_pending_order(&mut self, intent_id: &str, order_id: &OrderId) {
        if let Some(state) = self.intents.get_mut(intent_id) {
            state
                .pending_orders
                .retain(|order| &order.order_id != order_id);
            state.pending_order_due_unix_nanos.remove(order_id);
        }
    }

    pub(crate) fn clear_pending_orders(&mut self, intent_id: &str) {
        if let Some(state) = self.intents.get_mut(intent_id) {
            state.pending_orders.clear();
            state.pending_order_due_unix_nanos.clear();
        }
    }

    pub(crate) fn update_quote_refresh(&mut self, intent_id: &str, version: u64, now: u64) {
        if let Some(state) = self.intents.get_mut(intent_id) {
            state.quote_version = version;
            state.last_quote_refresh_unix_nanos = Some(now.into());
        }
    }

    pub(crate) fn attach_intent_plan_order(
        &mut self,
        intent_id: &str,
        leg_id: &str,
        order_id: &str,
    ) -> Result<(), String> {
        let (plan_id, leg_id) = {
            let plan = self
                .intents
                .get_mut(intent_id)
                .ok_or_else(|| "intent plan owner is missing".to_string())?
                .plan
                .as_mut()
                .ok_or_else(|| "intent has no execution plan".to_string())?;
            let plan_id = plan.plan_id.clone();
            let leg = plan
                .legs
                .iter_mut()
                .find(|leg| leg.leg_id == leg_id)
                .ok_or_else(|| "intent plan leg is missing".to_string())?;
            if !leg.order_ids.iter().any(|value| value == order_id) {
                leg.order_ids.push(OrderId::new(order_id.to_owned())?);
            }
            if leg.lifecycle == LegLifecycle::Pending {
                leg.transition(LegLifecycle::Ready, "child order prepared")?;
            }
            if leg.lifecycle == LegLifecycle::Ready {
                leg.transition(LegLifecycle::Executing, "child order submitted")?;
            }
            (plan_id, leg.leg_id.clone())
        };
        self.attach_plan_identity(order_id, plan_id, leg_id);
        Ok(())
    }

    pub(crate) fn refresh_intent_plan_progress(
        &mut self,
        intent_id: &str,
        orders: &[ExecutionOrder],
    ) -> Result<(), String> {
        let Some(plan) = self
            .intents
            .get_mut(intent_id)
            .and_then(|state| state.plan.as_mut())
        else {
            return Ok(());
        };
        for leg in &mut plan.legs {
            let leg_orders: Vec<_> = orders
                .iter()
                .filter(|order| leg.order_ids.iter().any(|id| id == &order.order_id))
                .collect();
            leg.completed_quantity = leg_orders
                .iter()
                .try_fold(Quantity::ZERO, |total, order| {
                    total.checked_add(order.filled_quantity)
                })
                .map_err(|_| "execution leg completed quantity overflow".to_string())?;
            let next = if leg_orders.is_empty() {
                leg.lifecycle
            } else if leg_orders
                .iter()
                .any(|order| order.status == ExecutionOrderStatus::Failed)
            {
                LegLifecycle::Failed
            } else if leg_orders
                .iter()
                .all(|order| order.status == ExecutionOrderStatus::Filled)
            {
                LegLifecycle::Satisfied
            } else if leg_orders.iter().any(|order| {
                matches!(
                    order.status,
                    ExecutionOrderStatus::PartiallyFilled | ExecutionOrderStatus::Filled
                )
            }) {
                LegLifecycle::PartiallyFilled
            } else if leg_orders.iter().any(|order| !order.status.terminal()) {
                LegLifecycle::Executing
            } else {
                leg.lifecycle
            };
            leg.transition(next, "child order progress updated")?;
        }
        Ok(())
    }

    pub(crate) fn restore_intent_events(&mut self, events: Vec<IntentEvent>) {
        self.intent_events = events;
        self.pending_intent_events = self.intent_events.clone();
    }

    /// Apply one intent lifecycle fact atomically inside the mutable state
    /// owner. Sequence allocation and both durable/current projections must
    /// never be assembled independently by the application facade.
    pub(crate) fn apply_intent_event(
        &mut self,
        mut event: IntentEvent,
    ) -> Result<(IntentEvent, IntentState), String> {
        let state = self
            .intents
            .get_mut(event.intent_id.as_str())
            .ok_or_else(|| "intent event references unknown intent".to_string())?;
        state.status = event.status;
        state.order_ids.extend(event.order_ids.iter().cloned());
        state.order_ids.sort();
        state.order_ids.dedup();
        state.completed_quantity = event.completed_quantity;
        state.updated_at_unix_nanos = event.occurred_at_unix_nanos;
        state.reason = event.reason.clone();
        self.event_sequence += 1;
        self.generation += 1;
        event.event_sequence = self.event_sequence.into();
        self.intent_events.push(event.clone());
        self.pending_intent_events.push(event.clone());
        Ok((event, state.clone()))
    }
}
