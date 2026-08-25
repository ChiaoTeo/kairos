//! Intent acceptance, planning handoff, and idempotent submission.

use super::super::*;
use super::planning::plan_simulated_intent;

impl ExecutionApplication {
    pub fn submit_intent(
        &mut self,
        intent: ExecuteStrategyIntent,
    ) -> Result<IntentState, ExecutionError> {
        self.accept_intent(intent, true)
    }

    fn accept_intent(
        &mut self,
        intent: ExecuteStrategyIntent,
        dispatch_due_orders: bool,
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
        if intent
            .strategy_decision_id
            .as_ref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err(ExecutionError::Invalid(
                "strategy_decision_id cannot be blank".into(),
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
            if existing.intent.strategy_decision_id != intent.strategy_decision_id {
                return Err(ExecutionError::Invalid(
                    "intent idempotent replay changed strategy_decision_id".into(),
                ));
            }
            if existing.status == IntentStatus::Rejected {
                return Err(ExecutionError::Invalid(existing.reason.clone()));
            }
            debug!(event = "intent_idempotent_replay", component = "execution", intent_id = %intent.intent_id, status = ?existing.status, "existing intent returned without creating a duplicate");
            return Ok(existing.clone());
        }
        let planned_orders = if let Some(planner) = self.intent_planner.as_mut() {
            planner
                .plan_intent(&intent)
                .map_err(ExecutionError::Invalid)?
        } else if self.live_trading {
            return Err(ExecutionError::Invalid(
                "live execution intent planning requires configured dependency facts".into(),
            ));
        } else {
            plan_simulated_intent(&intent)?
        };
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
                dormant_orders: Vec::new(),
                pending_order_due_unix_nanos: BTreeMap::new(),
                quote_version: 0,
                last_quote_refresh_unix_nanos: None,
                compensation_attempts: 0,
            };
            self.actor.insert_intent(state.clone());
            self.actor
                .insert_algorithm_run(AlgorithmRun::completed(intent.intent_id.clone()))
                .map_err(ExecutionError::Invalid)?;
            self.commit_intent(IntentEvent {
                intent_id: intent.intent_id.clone(),
                strategy_decision_id: intent.strategy_decision_id.clone(),
                event_sequence: 0.into(),
                previous_status: None,
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
        let mut plan = build_single_intent_plan(&intent, &planned_orders)?;
        let (algorithm_run, pending_orders, dormant_orders) = if intent.intent_type
            == IntentType::PairArbitrage
        {
            if let Some(policy) = intent.hedge_policy.as_ref() {
                if plan.legs.len() != 2 {
                    return Err(ExecutionError::Invalid(
                        "maker-taker pair execution requires exactly two plan legs".into(),
                    ));
                }
                let leader_target = plan
                    .legs
                    .iter()
                    .find(|leg| leg.leg_id == policy.leader_leg_id)
                    .map(|leg| leg.target_quantity)
                    .ok_or_else(|| ExecutionError::Invalid("leader leg is missing".into()))?;
                plan.legs
                    .iter()
                    .find(|leg| leg.leg_id == policy.hedge_leg_id)
                    .ok_or_else(|| ExecutionError::Invalid("hedge leg is missing".into()))?;
                let spec = MakerTakerHedgeSpec {
                    leader_leg_id: policy.leader_leg_id.clone(),
                    hedge_leg_id: policy.hedge_leg_id.clone(),
                    hedge_ratio: policy.ratio,
                    contract_multiplier: policy.contract_multiplier,
                    max_unhedged_quantity: policy.max_unhedged_quantity,
                    max_unhedged_duration: policy.max_unhedged_duration,
                    fallback_execution_route_ids: policy.fallback_execution_route_ids.clone(),
                };
                let hedge_target = spec
                    .required_hedge_quantity(leader_target)
                    .map_err(ExecutionError::Invalid)?;
                plan.legs
                    .iter_mut()
                    .find(|leg| leg.leg_id == policy.hedge_leg_id)
                    .expect("validated hedge leg")
                    .target_quantity = hedge_target;
                let run = AlgorithmRun::maker_taker_hedge(
                    intent.intent_id.clone(),
                    spec,
                    leader_target,
                    hedge_target,
                )
                .map_err(ExecutionError::Invalid)?;
                let (dormant, mut pending): (Vec<_>, Vec<_>) =
                    planned_orders.iter().cloned().partition(|order| {
                        intent_leg_id(&intent, order) == policy.hedge_leg_id.as_str()
                    });
                let hedge_template = dormant.first().ok_or_else(|| {
                    ExecutionError::Invalid("maker-taker hedge order is missing".into())
                })?;
                if hedge_template
                    .execution_route_id
                    .as_ref()
                    .is_some_and(|primary| {
                        policy
                            .fallback_execution_route_ids
                            .iter()
                            .any(|fallback| fallback == primary)
                    })
                {
                    return Err(ExecutionError::Invalid(
                        "hedge fallback route duplicates the primary hedge route".into(),
                    ));
                }
                let mut fallback_request = hedge_template.clone();
                fallback_request.order_type = OrderType::Market;
                fallback_request.limit_price = None;
                fallback_request.options.post_only = Some(false);
                fallback_request.options.time_in_force = None;
                fallback_request.options.split = None;
                fallback_request.options.maker = None;
                for route_id in &policy.fallback_execution_route_ids {
                    let configured = self.execution_routes.get(route_id).ok_or_else(|| {
                        ExecutionError::Invalid(format!(
                            "hedge fallback execution route {route_id} is not configured"
                        ))
                    })?;
                    let mut candidate = configured.candidate.clone();
                    // Readiness is a runtime fact. Admission verifies the stable route
                    // capability so a temporarily unavailable fallback can still recover.
                    candidate.ready = true;
                    super::super::orders::validate_execution_route(&fallback_request, &candidate)
                        .map_err(ExecutionError::Invalid)?;
                }
                if pending.iter().any(|order| order.limit_price.is_none()) {
                    return Err(ExecutionError::Invalid(
                        "maker-first leader requires a limit price".into(),
                    ));
                }
                for order in &mut pending {
                    order.options.post_only = Some(true);
                }
                (run, pending, dormant)
            } else {
                (
                    AlgorithmRun::immediate(
                        intent.intent_id.clone(),
                        plan.legs
                            .iter()
                            .map(|leg| (leg.leg_id.clone(), leg.target_quantity)),
                    )
                    .map_err(ExecutionError::Invalid)?,
                    planned_orders.clone(),
                    Vec::new(),
                )
            }
        } else {
            (
                AlgorithmRun::immediate(
                    intent.intent_id.clone(),
                    plan.legs
                        .iter()
                        .map(|leg| (leg.leg_id.clone(), leg.target_quantity)),
                )
                .map_err(ExecutionError::Invalid)?,
                planned_orders.clone(),
                Vec::new(),
            )
        };
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
            pending_orders: pending_orders.clone(),
            dormant_orders,
            pending_order_due_unix_nanos: scheduled_order_due(&intent, &pending_orders, now),
            quote_version: 0,
            last_quote_refresh_unix_nanos: None,
            compensation_attempts: 0,
        };
        self.actor.insert_intent(state.clone());
        self.actor
            .insert_algorithm_run(algorithm_run)
            .map_err(ExecutionError::Invalid)?;
        self.commit_intent(IntentEvent {
            intent_id: intent.intent_id.clone(),
            strategy_decision_id: intent.strategy_decision_id.clone(),
            event_sequence: 0.into(),
            previous_status: None,
            status: IntentStatus::Accepted,
            order_ids: Vec::new(),
            completed_quantity: completed_quantity(&intent, 0),
            occurred_at_unix_nanos: now.into(),
            reason: String::new(),
            dependency_watermarks: state.dependency_watermarks.clone(),
        })?;
        if dispatch_due_orders {
            self.advance_due_intent_orders(business_now, usize::MAX)?;
        }
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
        self.accept_intent_with_idempotency(intent, idempotency_key, true)
    }

    pub(crate) fn accept_intent_with_idempotency_deferred(
        &mut self,
        intent: ExecuteStrategyIntent,
        idempotency_key: String,
    ) -> Result<(IntentState, bool), ExecutionError> {
        self.accept_intent_with_idempotency(intent, idempotency_key, false)
    }

    fn accept_intent_with_idempotency(
        &mut self,
        intent: ExecuteStrategyIntent,
        idempotency_key: String,
        dispatch_due_orders: bool,
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
            if state.intent.strategy_decision_id != intent.strategy_decision_id {
                return Err(ExecutionError::Invalid(
                    "idempotency key replay changed strategy_decision_id".into(),
                ));
            }
            if state.status == IntentStatus::Rejected {
                return Err(ExecutionError::Invalid(state.reason.clone()));
            }
            let state = state.clone();
            return Ok((state, true));
        }
        let state = match self.accept_intent(intent.clone(), dispatch_due_orders) {
            Ok(state) => state,
            Err(error) => {
                if !self.actor.contains_intent(intent.intent_id.as_str()) {
                    let rejected_intent_id = intent.intent_id.to_string();
                    let reason = match &error {
                        ExecutionError::Invalid(reason) => reason.clone(),
                        _ => error.to_string(),
                    };
                    self.record_rejected_intent(intent, reason)?;
                    self.actor
                        .record_intent_idempotency(idempotency_key, rejected_intent_id);
                    self.persist_snapshot()?;
                }
                return Err(error);
            },
        };
        self.actor
            .record_intent_idempotency(idempotency_key, state.intent.intent_id.to_string());
        self.persist_snapshot()?;
        Ok((state, false))
    }

    fn record_rejected_intent(
        &mut self,
        intent: ExecuteStrategyIntent,
        reason: String,
    ) -> Result<(), ExecutionError> {
        let now = intent
            .source_event_time_unix_nanos
            .map(UnixNanos::get)
            .unwrap_or_else(now_nanos);
        let state = IntentState {
            intent: intent.clone(),
            status: IntentStatus::Rejected,
            order_ids: Vec::new(),
            plan: None,
            completed_quantity: completed_quantity(&intent, 0),
            updated_at_unix_nanos: now.into(),
            reason: reason.clone(),
            dependency_watermarks: self
                .intent_planner
                .as_ref()
                .map(|planner| planner.dependency_watermarks())
                .unwrap_or_default(),
            pending_orders: Vec::new(),
            dormant_orders: Vec::new(),
            pending_order_due_unix_nanos: BTreeMap::new(),
            quote_version: 0,
            last_quote_refresh_unix_nanos: None,
            compensation_attempts: 0,
        };
        self.actor.insert_intent(state.clone());
        self.commit_intent(IntentEvent {
            intent_id: intent.intent_id,
            strategy_decision_id: intent.strategy_decision_id,
            event_sequence: 0.into(),
            previous_status: None,
            status: IntentStatus::Rejected,
            order_ids: Vec::new(),
            completed_quantity: state.completed_quantity,
            occurred_at_unix_nanos: now.into(),
            reason,
            dependency_watermarks: state.dependency_watermarks,
        })
    }
}
