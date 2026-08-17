//! Intent, plan, hedge, split, and maker use cases.

use super::*;

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
            execution_access_id: template.execution_access_id.clone(),
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

    pub fn submit_intent(
        &mut self,
        intent: ExecuteStrategyIntent,
    ) -> Result<IntentState, ExecutionError> {
        info!(event = "intent_received", component = "execution", intent_id = %intent.intent_id, strategy_id = %intent.strategy_id, account_count = intent.account_ids.len(), "strategy intent received");
        if intent.intent_id.as_str().trim().is_empty()
            || intent.strategy_id.trim().is_empty()
            || intent.launch_id.trim().is_empty()
            || intent.instance_id.trim().is_empty()
            || intent.instrument_id.as_str().trim().is_empty()
            || intent.segment_key.as_str().trim().is_empty()
        {
            return Err(ExecutionError::Invalid(
                "intent identity is required".into(),
            ));
        }
        if intent.account_ids.is_empty()
            || intent
                .account_ids
                .iter()
                .any(|id| id.as_str().trim().is_empty())
        {
            return Err(ExecutionError::Invalid(
                "intent must target at least one account".into(),
            ));
        }
        if intent.target_quantity.mantissa() < 0 {
            return Err(ExecutionError::Invalid(
                "intent target quantity cannot be negative".into(),
            ));
        }
        if intent.min_edge_bps.is_some_and(|value| value > 1_000_000)
            || intent
                .max_slippage_bps
                .is_some_and(|value| value > 1_000_000)
            || intent
                .estimated_fee_bps
                .is_some_and(|value| value > 1_000_000)
        {
            return Err(ExecutionError::Invalid(
                "intent execution constraints are out of range".into(),
            ));
        }
        intent
            .hedge_policy
            .as_ref()
            .map(HedgePolicy::validate)
            .transpose()
            .map_err(ExecutionError::Invalid)?;
        intent
            .order_options
            .split
            .as_ref()
            .map(SplitOrderPolicy::validate)
            .transpose()
            .map_err(ExecutionError::Invalid)?;
        intent
            .order_options
            .maker
            .as_ref()
            .map(MakerExecutionPolicy::validate)
            .transpose()
            .map_err(ExecutionError::Invalid)?;
        if intent.intent_type == IntentType::PairArbitrage {
            if intent.legs.len() < 2
                || !intent.legs.iter().any(|leg| leg.side == OrderSide::Buy)
                || !intent.legs.iter().any(|leg| leg.side == OrderSide::Sell)
            {
                return Err(ExecutionError::Invalid(
                    "pair arbitrage requires at least one buy leg and one sell leg".into(),
                ));
            }
            if let Some(policy) = intent.hedge_policy.as_ref() {
                if !intent
                    .legs
                    .iter()
                    .any(|leg| leg.leg_id == policy.leader_leg_id)
                    || !intent
                        .legs
                        .iter()
                        .any(|leg| leg.leg_id == policy.hedge_leg_id)
                {
                    return Err(ExecutionError::Invalid(
                        "hedge policy references a leg outside the pair plan".into(),
                    ));
                }
            }
        }
        if intent.intent_type == IntentType::OptionSpread {
            if intent.legs.len() != 2 {
                return Err(ExecutionError::Invalid(
                    "option spread requires exactly two legs".into(),
                ));
            }
            if intent.completion_policy != CompletionPolicy::AllOrNothing
                || intent.failure_policy != FailurePolicy::CancelRemaining
            {
                return Err(ExecutionError::Invalid(
                    "option spread requires all-or-nothing and cancel-remaining policies".into(),
                ));
            }
            let short = intent.legs.iter().find(|leg| leg.side == OrderSide::Sell);
            let long = intent.legs.iter().find(|leg| leg.side == OrderSide::Buy);
            let (Some(short), Some(long)) = (short, long) else {
                return Err(ExecutionError::Invalid(
                    "option spread requires one sell leg and one buy leg".into(),
                ));
            };
            if short.instrument_id == long.instrument_id {
                return Err(ExecutionError::Invalid(
                    "option spread legs must use different instruments".into(),
                ));
            }
            if short.quantity != long.quantity {
                return Err(ExecutionError::Invalid(
                    "option spread legs must have equal quantity".into(),
                ));
            }
            if intent.minimum_net_credit.is_none_or(Money::is_negative)
                || intent
                    .maximum_loss
                    .is_none_or(|value| value.is_zero() || value.is_negative())
            {
                return Err(ExecutionError::Invalid(
                    "option spread requires non-negative minimum credit and positive maximum loss"
                        .into(),
                ));
            }
        }
        for leg in &intent.legs {
            leg.options
                .split
                .as_ref()
                .map(SplitOrderPolicy::validate)
                .transpose()
                .map_err(ExecutionError::Invalid)?;
            leg.options
                .maker
                .as_ref()
                .map(MakerExecutionPolicy::validate)
                .transpose()
                .map_err(ExecutionError::Invalid)?;
        }
        if let Some(existing) = self.actor.intent(intent.intent_id.as_str()) {
            debug!(event = "intent_idempotent_replay", component = "execution", intent_id = %intent.intent_id, status = ?existing.status, "existing intent returned without creating a duplicate");
            return Ok(existing.clone());
        }
        let planned_orders = self
            .intent_planner
            .as_mut()
            .ok_or_else(|| {
                ExecutionError::Invalid("execution intent planner is not configured".into())
            })?
            .plan_intent(&intent)
            .map_err(ExecutionError::Invalid)?;
        let planned_orders = expand_child_orders(&intent, planned_orders)?;
        let business_now = intent
            .source_event_time_unix_nanos
            .map(UnixNanos::get)
            .unwrap_or_else(now_nanos);
        if planned_orders.is_empty() {
            let now = business_now;
            let state = IntentState {
                intent: intent.clone(),
                status: IntentStatus::Satisfied,
                order_ids: Vec::new(),
                plan: None,
                completed_quantity: intent.target_quantity,
                updated_at_unix_nanos: now.into(),
                reason: "target position already satisfied".into(),
                dependency_watermarks: self
                    .intent_planner
                    .as_ref()
                    .map(|planner| planner.dependency_watermarks())
                    .unwrap_or_default(),
                pending_orders: Vec::new(),
                pending_order_due_unix_nanos: BTreeMap::new(),
                quote_version: 0,
                last_quote_refresh_unix_nanos: None,
                compensation_attempts: 0,
            };
            self.actor.insert_intent(state.clone());
            self.commit_intent(IntentEvent {
                intent_id: intent.intent_id.clone(),
                event_sequence: 0.into(),
                status: IntentStatus::Satisfied,
                order_ids: Vec::new(),
                completed_quantity: state.completed_quantity,
                occurred_at_unix_nanos: now.into(),
                reason: state.reason.clone(),
                dependency_watermarks: state.dependency_watermarks.clone(),
            })?;
            info!(event = "intent_satisfied", component = "execution", intent_id = %state.intent.intent_id, reason = %state.reason, "intent already satisfied");
            return Ok(state);
        }
        if planned_orders.iter().any(|order| {
            order
                .intent_id
                .as_ref()
                .is_none_or(|id| id.as_str() != intent.intent_id.as_str())
                || !intent.account_ids.iter().any(|id| id == &order.account_id)
        }) {
            return Err(ExecutionError::Invalid(
                "intent plan contains an order outside its intent accounts".into(),
            ));
        }
        let now = business_now;
        let plan = build_single_intent_plan(&intent, &planned_orders)?;
        let state = IntentState {
            intent: intent.clone(),
            status: IntentStatus::Accepted,
            order_ids: Vec::new(),
            plan: Some(plan),
            completed_quantity: completed_quantity(&intent, 0),
            updated_at_unix_nanos: now.into(),
            reason: String::new(),
            dependency_watermarks: self
                .intent_planner
                .as_ref()
                .map(|planner| planner.dependency_watermarks())
                .unwrap_or_default(),
            pending_orders: planned_orders.clone(),
            pending_order_due_unix_nanos: scheduled_order_due(&intent, &planned_orders, now),
            quote_version: 0,
            last_quote_refresh_unix_nanos: None,
            compensation_attempts: 0,
        };
        self.actor.insert_intent(state.clone());
        self.commit_intent(IntentEvent {
            intent_id: intent.intent_id.clone(),
            event_sequence: 0.into(),
            status: IntentStatus::Accepted,
            order_ids: Vec::new(),
            completed_quantity: completed_quantity(&intent, 0),
            occurred_at_unix_nanos: now.into(),
            reason: String::new(),
            dependency_watermarks: state.dependency_watermarks.clone(),
        })?;
        self.advance_due_intent_orders(business_now, usize::MAX)?;
        Ok(self
            .actor
            .intent(intent.intent_id.as_str())
            .cloned()
            .unwrap_or(state))
    }

    pub fn submit_intent_with_idempotency(
        &mut self,
        intent: ExecuteStrategyIntent,
        idempotency_key: String,
    ) -> Result<(IntentState, bool), ExecutionError> {
        info!(event = "intent_idempotency_check", component = "execution", intent_id = %intent.intent_id, idempotency_key = %idempotency_key, "checking intent idempotency");
        if idempotency_key.trim().is_empty() {
            return Err(ExecutionError::Invalid(
                "idempotency_key is required".into(),
            ));
        }
        if let Some(state) = self
            .actor
            .intent_for_idempotency_key(&idempotency_key)
            .map_err(ExecutionError::Persistence)?
        {
            let state = state.clone();
            return Ok((state, true));
        }
        let state = self.submit_intent(intent)?;
        self.actor
            .record_intent_idempotency(idempotency_key, state.intent.intent_id.to_string());
        self.persist_snapshot()?;
        Ok((state, false))
    }

    /// Submit due child orders from durable Intent scheduling state.  The
    /// state loop calls this frequently; command submission therefore never
    /// blocks on a maker cadence or split interval.
    pub fn advance_due_intent_orders(
        &mut self,
        now_unix_nanos: u64,
        limit: usize,
    ) -> Result<usize, ExecutionError> {
        let mut submitted = 0;
        while submitted < limit {
            let next = self
                .actor
                .intents()
                .filter_map(|state| {
                    state
                        .pending_orders
                        .iter()
                        .filter_map(|order| {
                            let due = state
                                .pending_order_due_unix_nanos
                                .get(&order.order_id)
                                .copied()
                                .unwrap_or_else(|| now_unix_nanos.into());
                            (due.get() <= now_unix_nanos)
                                .then(|| (due, state.intent.intent_id.clone(), order.clone()))
                        })
                        .min_by_key(|(due, _, _)| *due)
                })
                .min_by_key(|(due, _, _)| *due);
            let Some((_, intent_id, request)) = next else {
                break;
            };
            let leg_id = intent_leg_id(
                &self
                    .actor
                    .intent(intent_id.as_str())
                    .ok_or_else(|| ExecutionError::Invalid("scheduled intent disappeared".into()))?
                    .intent,
                &request,
            );
            self.actor
                .remove_pending_order(intent_id.as_str(), &request.order_id);
            match self.submit(request.clone()) {
                Ok(order) => {
                    self.attach_plan_order(&intent_id, &leg_id, &order.order_id)?;
                    let state =
                        self.actor
                            .intent(intent_id.as_str())
                            .cloned()
                            .ok_or_else(|| {
                                ExecutionError::Invalid("scheduled intent disappeared".into())
                            })?;
                    self.commit_intent(IntentEvent {
                        intent_id: intent_id.clone(),
                        event_sequence: 0.into(),
                        status: IntentStatus::Executing,
                        order_ids: vec![order.order_id.clone()],
                        completed_quantity: state.completed_quantity,
                        occurred_at_unix_nanos: now_unix_nanos.into(),
                        reason: "child order created".into(),
                        dependency_watermarks: state.dependency_watermarks,
                    })?;
                    submitted += 1;
                }
                Err(error) => {
                    warn!(event = "scheduled_child_order_failed", component = "execution", intent_id = %intent_id, error = %error, "scheduled child order failed");
                    let failure_policy = self
                        .actor
                        .intent(intent_id.as_str())
                        .map(|state| state.intent.failure_policy)
                        .unwrap_or(FailurePolicy::CancelRemaining);
                    let cancel_siblings = matches!(failure_policy, FailurePolicy::CancelRemaining)
                        || self.actor.intent(intent_id.as_str()).is_some_and(|state| {
                            state.intent.intent_type == IntentType::PairArbitrage
                        });
                    if cancel_siblings {
                        let siblings = self
                            .actor
                            .intent(intent_id.as_str())
                            .map(|state| state.order_ids.clone())
                            .unwrap_or_default();
                        for order_id in siblings {
                            if self
                                .actor
                                .order_map()
                                .get(order_id.as_str())
                                .is_some_and(|order| !order.status.terminal())
                            {
                                let _ = self.cancel(CancelOrder {
                                    order_id,
                                    reason: "pair leg submission failed".into(),
                                });
                            }
                        }
                    }
                    let completed_quantity = self
                        .actor
                        .intent(intent_id.as_str())
                        .map(|state| state.completed_quantity)
                        .unwrap_or_default();
                    self.commit_intent(IntentEvent {
                        intent_id,
                        event_sequence: 0.into(),
                        status: IntentStatus::Failed,
                        order_ids: Vec::new(),
                        completed_quantity,
                        occurred_at_unix_nanos: now_unix_nanos.into(),
                        reason: error.to_string(),
                        dependency_watermarks: self.dependency_watermarks(),
                    })?;
                    return Err(error);
                }
            }
        }
        if submitted > 0 {
            self.persist_snapshot()?;
        }
        Ok(submitted)
    }

    pub fn cancel_intent(&mut self, request: CancelIntent) -> Result<IntentState, ExecutionError> {
        let state = self
            .actor
            .intent(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown intent".into()))?;
        if matches!(
            state.status,
            IntentStatus::Satisfied
                | IntentStatus::Canceled
                | IntentStatus::Rejected
                | IntentStatus::Expired
                | IntentStatus::Failed
                | IntentStatus::ReconciliationRequired
        ) {
            return Err(ExecutionError::Invalid("intent is terminal".into()));
        }
        self.actor.clear_pending_orders(request.intent_id.as_str());
        self.commit_intent(IntentEvent {
            intent_id: request.intent_id.clone(),
            event_sequence: 0.into(),
            status: IntentStatus::CancelRequested,
            order_ids: state.order_ids.clone(),
            completed_quantity: state.completed_quantity,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: if request.reason.trim().is_empty() {
                "intent cancellation requested".into()
            } else {
                request.reason.clone()
            },
            dependency_watermarks: state.dependency_watermarks.clone(),
        })?;
        for order_id in state.order_ids {
            let active = self
                .actor
                .order_map()
                .get(order_id.as_str())
                .is_some_and(|order| !order.status.terminal());
            if active {
                self.cancel(CancelOrder {
                    order_id,
                    reason: request.reason.clone(),
                })?;
            }
        }
        let has_active = self
            .actor
            .intent(request.intent_id.as_str())
            .map(|value| {
                value.order_ids.iter().any(|order_id| {
                    self.actor
                        .order_map()
                        .get(order_id.as_str())
                        .is_some_and(|order| !order.status.terminal())
                })
            })
            .unwrap_or(false);
        if has_active {
            self.refresh_intent(request.intent_id.as_str())?;
        } else {
            let current = self
                .actor
                .intent(request.intent_id.as_str())
                .cloned()
                .ok_or_else(|| {
                    ExecutionError::Invalid("intent disappeared during cancellation".into())
                })?;
            self.commit_intent(IntentEvent {
                intent_id: request.intent_id.clone(),
                event_sequence: 0.into(),
                status: IntentStatus::Canceled,
                order_ids: Vec::new(),
                completed_quantity: current.completed_quantity,
                occurred_at_unix_nanos: now_nanos().into(),
                reason: "all pending and active child orders canceled".into(),
                dependency_watermarks: current.dependency_watermarks,
            })?;
        }
        self.actor
            .intent(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("intent disappeared during cancellation".into()))
    }

    pub fn expire_intent(&mut self, request: ExpireIntent) -> Result<IntentState, ExecutionError> {
        let state = self
            .actor
            .intent(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown intent".into()))?;
        if matches!(
            state.status,
            IntentStatus::Satisfied
                | IntentStatus::Canceled
                | IntentStatus::Rejected
                | IntentStatus::Expired
                | IntentStatus::Failed
                | IntentStatus::ReconciliationRequired
        ) {
            return Err(ExecutionError::Invalid("intent is terminal".into()));
        }
        self.actor.clear_pending_orders(request.intent_id.as_str());
        self.commit_intent(IntentEvent {
            intent_id: request.intent_id.clone(),
            event_sequence: 0.into(),
            status: IntentStatus::Expired,
            order_ids: state.order_ids.clone(),
            completed_quantity: state.completed_quantity,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: if request.reason.trim().is_empty() {
                "intent expired".into()
            } else {
                request.reason
            },
            dependency_watermarks: state.dependency_watermarks,
        })?;
        for order_id in state.order_ids {
            if self
                .actor
                .order_map()
                .get(order_id.as_str())
                .is_some_and(|order| !order.status.terminal())
            {
                let _ = self.cancel(CancelOrder {
                    order_id,
                    reason: "parent intent expired".into(),
                });
            }
        }
        // Child cancellation refreshes the parent intent. Re-assert the
        // deadline outcome after those child lifecycle events so expiration
        // remains the authoritative terminal reason.
        self.commit_intent(IntentEvent {
            intent_id: request.intent_id.clone(),
            event_sequence: 0.into(),
            status: IntentStatus::Expired,
            order_ids: Vec::new(),
            completed_quantity: self
                .actor
                .intent(request.intent_id.as_str())
                .map(|value| value.completed_quantity)
                .unwrap_or_default(),
            occurred_at_unix_nanos: now_nanos().into(),
            reason: "intent expired after child cancellation".into(),
            dependency_watermarks: self
                .actor
                .intent(request.intent_id.as_str())
                .map(|value| value.dependency_watermarks.clone())
                .unwrap_or_default(),
        })?;
        self.actor
            .intent(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("intent disappeared during expiration".into()))
    }

    pub fn expire_due_intents(&mut self, now_unix_nanos: u64) -> Result<usize, ExecutionError> {
        let due = self
            .actor
            .intents()
            .filter(|state| {
                !matches!(
                    state.status,
                    IntentStatus::Satisfied
                        | IntentStatus::Canceled
                        | IntentStatus::Rejected
                        | IntentStatus::Expired
                        | IntentStatus::Failed
                        | IntentStatus::ReconciliationRequired
                ) && state
                    .intent
                    .deadline_unix_nanos
                    .is_some_and(|deadline| deadline <= now_unix_nanos.into())
            })
            .map(|state| state.intent.intent_id.clone())
            .collect::<Vec<_>>();
        let count = due.len();
        for intent_id in due {
            self.expire_intent(ExpireIntent {
                intent_id,
                reason: "intent deadline reached".into(),
            })?;
        }
        Ok(count)
    }

    pub(super) fn commit_intent(&mut self, event: IntentEvent) -> Result<(), ExecutionError> {
        let (event, state) = self
            .actor
            .apply_intent_event(event)
            .map_err(ExecutionError::Invalid)?;
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            store
                .commit_intent_event(&event, &snapshot)
                .map_err(ExecutionError::Persistence)?;
        }
        self.pending_business_events
            .push_back(ExecutionBusinessEvent {
                sequence: event.event_sequence,
                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                changes: vec![ExecutionBusinessChange::Intent(state)],
            });
        Ok(())
    }

    pub(super) fn persist_snapshot(&mut self) -> Result<(), ExecutionError> {
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            store.save(&snapshot).map_err(ExecutionError::Persistence)?;
        }
        Ok(())
    }

    pub(super) fn refresh_intent(&mut self, intent_id: &str) -> Result<(), ExecutionError> {
        let Some(state) = self.actor.intent(intent_id).cloned() else {
            return Ok(());
        };
        let orders: Vec<ExecutionOrder> = state
            .order_ids
            .iter()
            .filter_map(|order_id| self.actor.order_map().get(order_id.as_str()).cloned())
            .collect();
        if orders.is_empty() {
            return Ok(());
        }
        let completed = orders
            .iter()
            .try_fold(Quantity::ZERO, |total, order| {
                total.checked_add(order.filled_quantity)
            })
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
        self.refresh_plan_progress(intent_id, &orders)?;
        let has_pending = self
            .actor
            .intent(intent_id)
            .is_some_and(|value| !value.pending_orders.is_empty());
        let all_filled = orders
            .iter()
            .all(|order| order.status == ExecutionOrderStatus::Filled);
        let all_canceled = orders
            .iter()
            .all(|order| order.status == ExecutionOrderStatus::Canceled);
        let has_active = orders.iter().any(|order| !order.status.terminal());
        let has_failed = orders.iter().any(|order| {
            matches!(
                order.status,
                ExecutionOrderStatus::Rejected
                    | ExecutionOrderStatus::Expired
                    | ExecutionOrderStatus::Unknown
                    | ExecutionOrderStatus::Failed
            )
        });
        let target_reached = state.intent.completion_policy
            == CompletionPolicy::TargetQuantityReached
            && state.intent.target_quantity > Quantity::ZERO
            && completed >= state.intent.target_quantity;
        let hedge_within_tolerance =
            if state.intent.completion_policy == CompletionPolicy::HedgeWithinTolerance {
                self.hedge_requirement(intent_id)
                    .ok()
                    .flatten()
                    .is_some_and(|requirement| requirement.within_tolerance)
            } else {
                false
            };
        let best_effort_complete = state.intent.completion_policy == CompletionPolicy::BestEffort
            && !has_active
            && completed > Quantity::ZERO;
        let policy_satisfied =
            !has_active && (target_reached || hedge_within_tolerance || best_effort_complete);
        let status = if (all_filled || policy_satisfied) && !has_pending && !has_failed {
            IntentStatus::Satisfied
        } else if all_canceled && !has_pending {
            IntentStatus::Canceled
        } else if has_pending {
            if completed > Quantity::ZERO {
                IntentStatus::PartiallyFilled
            } else {
                IntentStatus::Executing
            }
        } else if let Some(plan) = self
            .actor
            .intent(intent_id)
            .and_then(|state| state.plan.as_ref())
        {
            match plan.lifecycle(orders.iter().map(|order| order.status)) {
                crate::domain::IntentLifecycle::Satisfied => IntentStatus::Satisfied,
                crate::domain::IntentLifecycle::PartiallyFilled => IntentStatus::PartiallyFilled,
                crate::domain::IntentLifecycle::ReconciliationRequired => {
                    IntentStatus::ReconciliationRequired
                }
                crate::domain::IntentLifecycle::Failed => IntentStatus::Failed,
                crate::domain::IntentLifecycle::Canceled => IntentStatus::Canceled,
                _ if has_active => {
                    if completed > Quantity::ZERO {
                        IntentStatus::PartiallyFilled
                    } else {
                        IntentStatus::Executing
                    }
                }
                _ => IntentStatus::Executing,
            }
        } else if has_active {
            if orders
                .iter()
                .any(|order| order.status == ExecutionOrderStatus::Unknown)
            {
                IntentStatus::ReconciliationRequired
            } else if completed > Quantity::ZERO {
                IntentStatus::PartiallyFilled
            } else {
                IntentStatus::Executing
            }
        } else if has_failed {
            if completed > Quantity::ZERO {
                IntentStatus::PartiallyFilled
            } else {
                IntentStatus::Failed
            }
        } else {
            IntentStatus::Executing
        };
        if state.status == status && state.completed_quantity == completed {
            return Ok(());
        }
        self.commit_intent(IntentEvent {
            intent_id: typed_intent_id(intent_id),
            event_sequence: 0.into(),
            status,
            order_ids: state.order_ids,
            completed_quantity: completed,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: match status {
                IntentStatus::Satisfied => "all child orders filled".into(),
                IntentStatus::PartiallyFilled => "child orders partially filled".into(),
                IntentStatus::Canceled => "all child orders canceled".into(),
                IntentStatus::Failed => "all child orders failed".into(),
                IntentStatus::ReconciliationRequired => {
                    "child order reconciliation required".into()
                }
                _ => "child execution progressing".into(),
            },
            dependency_watermarks: state.dependency_watermarks,
        })
    }

    pub(super) fn attach_plan_order(
        &mut self,
        intent_id: &str,
        leg_id: &str,
        order_id: &str,
    ) -> Result<(), ExecutionError> {
        self.actor
            .attach_intent_plan_order(intent_id, leg_id, order_id)
            .map_err(ExecutionError::Invalid)
    }

    pub(super) fn refresh_plan_progress(
        &mut self,
        intent_id: &str,
        orders: &[ExecutionOrder],
    ) -> Result<(), ExecutionError> {
        self.actor
            .refresh_intent_plan_progress(intent_id, orders)
            .map_err(ExecutionError::Invalid)
    }
}
