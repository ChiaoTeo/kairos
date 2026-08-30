//! Intent, plan, hedge, split, and maker use cases.

use std::collections::BTreeSet;

use super::*;

mod lifecycle;
mod planning;
mod submission;

pub(crate) struct PreparedCompensatingHedge {
    pub(crate) intent_id: IntentId,
    pub(crate) leg_id: LegId,
    pub(crate) request: SubmitOrder,
}

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
        let Some(policy) = state.intent.algorithm.hedge_policy() else {
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
        let algorithm_exposure = self
            .actor
            .algorithm_run(intent_id)
            .and_then(|run| run.exposure.as_ref());
        let unhedged_since = algorithm_exposure.and_then(|exposure| exposure.unhedged_since);
        let exposure_deadline = policy
            .max_unhedged_duration
            .zip(unhedged_since)
            .map(|(duration, since)| {
                since
                    .get()
                    .checked_add(duration.get())
                    .map(UnixNanos::new)
                    .ok_or_else(|| ExecutionError::Invalid("hedge deadline overflow".into()))
            })
            .transpose()?;
        Ok(Some(HedgeRequirement {
            intent_id: typed_intent_id(intent_id),
            leader_leg_id: policy.leader_leg_id.clone(),
            hedge_leg_id: policy.hedge_leg_id.clone(),
            leader_filled_quantity: leader,
            hedge_filled_quantity: hedge,
            required_hedge_quantity: required,
            unhedged_quantity: unhedged,
            max_unhedged_quantity: policy.max_unhedged_quantity,
            unhedged_since,
            max_unhedged_duration: policy.max_unhedged_duration,
            exposure_deadline,
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
        business_time_unix_nanos: u64,
    ) -> Result<(), ExecutionError> {
        if self
            .actor
            .algorithm_run(intent_id)
            .is_some_and(|run| matches!(run.spec, ExecutionAlgorithmSpec::MakerTakerHedge(_)))
        {
            self.drive_maker_taker_hedge(intent_id, business_time_unix_nanos)?;
            let due_at = self
                .actor
                .algorithm_run(intent_id)
                .and_then(|run| run.last_decision_at)
                .map(UnixNanos::get)
                .unwrap_or(business_time_unix_nanos)
                .max(business_time_unix_nanos)
                .max(self.business_time_unix_nanos().unwrap_or_default());
            self.advance_due_intent_orders(due_at, usize::MAX)?;
            return Ok(());
        }
        let Some(prepared) = self.prepare_compensating_hedge(intent_id)? else {
            return Ok(());
        };
        match self.submit(prepared.request.clone()) {
            Ok(order) => self.complete_compensating_hedge(&prepared, &order),
            Err(error) => self.fail_compensating_hedge(&prepared, &error),
        }
    }

    pub(in crate::application) fn drive_maker_taker_hedge(
        &mut self,
        intent_id: &str,
        business_time_unix_nanos: u64,
    ) -> Result<(), ExecutionError> {
        let state = self
            .actor
            .intent(intent_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("hedge intent disappeared".into()))?;
        let run = self
            .actor
            .algorithm_run(intent_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("hedge algorithm run disappeared".into()))?;
        let ExecutionAlgorithmSpec::MakerTakerHedge(spec) = &run.spec else {
            return Ok(());
        };
        let exposure = run
            .exposure
            .as_ref()
            .ok_or_else(|| ExecutionError::Invalid("hedge exposure ledger is missing".into()))?;
        if exposure.unhedged_after_commitment.is_zero() {
            return Ok(());
        }
        let decision_time = run
            .last_decision_at
            .map(|last| last.get().max(business_time_unix_nanos))
            .unwrap_or(business_time_unix_nanos);
        if !spec
            .hedge_due(exposure, decision_time.into())
            .map_err(ExecutionError::Invalid)?
        {
            if spec.max_unhedged_duration.is_some() && !exposure.unhedged_filled_quantity.is_zero()
            {
                let decision = decide_maker_taker_hedge(
                    &run,
                    AlgorithmInput {
                        business_time: decision_time.into(),
                        ready_children: Vec::new(),
                    },
                )
                .map_err(ExecutionError::Invalid)?;
                self.actor
                    .apply_algorithm_decision(intent_id, decision)
                    .map_err(ExecutionError::Invalid)?;
                self.persist_snapshot()?;
            }
            return Ok(());
        }
        let hedge_failed = run.legs.iter().any(|leg| {
            leg.leg_id == spec.hedge_leg_id
                && leg.lifecycle == crate::domain::AlgorithmLegLifecycle::Failed
        });
        let template = state
            .dormant_orders
            .iter()
            .find(|order| intent_leg_id(&state.intent, order) == spec.hedge_leg_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("dormant hedge template is missing".into()))?;
        let fallback_route_id = hedge_failed
            .then(|| next_fallback_route_id(&run, spec))
            .flatten();
        if hedge_failed && fallback_route_id.is_none() {
            let reason =
                "taker hedge and configured fallback routes were exhausted with residual exposure";
            if self.drive_maker_taker_unwind(intent_id, business_time_unix_nanos, reason)? {
                return Ok(());
            }
            let current = self
                .actor
                .intent(intent_id)
                .cloned()
                .ok_or_else(|| ExecutionError::Invalid("unwind intent disappeared".into()))?;
            self.commit_intent(IntentEvent {
                intent_id: typed_intent_id(intent_id),
                strategy_decision_id: None,
                event_sequence: 0.into(),
                previous_status: None,
                status: IntentStatus::ReconciliationRequired,
                order_ids: Vec::new(),
                completed_quantity: current.completed_quantity,
                occurred_at_unix_nanos: business_time_unix_nanos.into(),
                reason: format!("automatic emergency unwind unavailable: {reason}"),
                dependency_watermarks: current.dependency_watermarks,
            })?;
            return Ok(());
        }
        let selected_route_id = fallback_route_id.or_else(|| template.execution_route_id.clone());
        let order_id = OrderId::new(format!(
            "{}:hedge:decision:{}",
            intent_id,
            run.decision_sequence.get().saturating_add(1)
        ))
        .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
        let decision = decide_maker_taker_hedge(
            &run,
            AlgorithmInput {
                business_time: decision_time.into(),
                ready_children: vec![AlgorithmChildCandidate {
                    order_id: order_id.clone(),
                    leg_id: spec.hedge_leg_id.clone(),
                    quantity: exposure.unhedged_after_commitment,
                    execution_style: AlgorithmExecutionStyle::TakerImmediate,
                    execution_route_id: selected_route_id.clone(),
                }],
            },
        )
        .map_err(ExecutionError::Invalid)?;
        let actions = self
            .actor
            .apply_algorithm_decision(intent_id, decision)
            .map_err(ExecutionError::Invalid)?;
        actions
            .into_iter()
            .find(|action| {
                matches!(
                    &action.kind,
                    AlgorithmActionKind::SubmitChild {
                        order_id: candidate_order_id,
                        execution_style: AlgorithmExecutionStyle::TakerImmediate,
                        ..
                    } if candidate_order_id == &order_id
                )
            })
            .ok_or_else(|| {
                ExecutionError::Invalid("maker-taker algorithm did not authorize a hedge".into())
            })?;

        let mut request = template;
        request.order_id = order_id;
        request.execution_route_id = selected_route_id;
        request.quantity = exposure.unhedged_after_commitment;
        request.order_type = OrderType::Market;
        request.limit_price = None;
        request.options.post_only = Some(false);
        request.options.time_in_force = None;
        request.options.split = None;
        request.options.maker = None;
        request.submitted_at_unix_nanos = Some(decision_time.into());
        self.actor
            .schedule_pending_order(intent_id, request, decision_time.into())
            .map_err(ExecutionError::Invalid)?;
        // The action and reconstructable request become durable together;
        // direct and managed runtimes dispatch them through the same due loop.
        self.persist_snapshot()
    }

    fn drive_maker_taker_unwind(
        &mut self,
        intent_id: &str,
        business_time_unix_nanos: u64,
        unwind_reason: &str,
    ) -> Result<bool, ExecutionError> {
        let state = self
            .actor
            .intent(intent_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unwind intent disappeared".into()))?;
        let Some(policy) = state.intent.algorithm.hedge_policy() else {
            return Ok(false);
        };
        if state.intent.failure_policy != FailurePolicy::Compensate
            || !policy.compensate_on_failure
            || state.compensation_attempts >= policy.max_compensation_attempts
        {
            return Ok(false);
        }
        let Some(max_slippage_bps) = state.intent.max_slippage_bps else {
            return Ok(false);
        };
        if max_slippage_bps >= 10_000 {
            return Ok(false);
        }
        let run = self
            .actor
            .algorithm_run(intent_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unwind algorithm run disappeared".into()))?;
        let ExecutionAlgorithmSpec::MakerTakerHedge(spec) = &run.spec else {
            return Ok(false);
        };
        let exposure = run
            .exposure
            .as_ref()
            .ok_or_else(|| ExecutionError::Invalid("unwind exposure ledger is missing".into()))?;
        if exposure.unhedged_after_commitment.is_zero() {
            return Ok(false);
        }
        let plan = state
            .plan
            .as_ref()
            .ok_or_else(|| ExecutionError::Invalid("unwind execution plan is missing".into()))?;
        let leader_leg = plan
            .legs
            .iter()
            .find(|leg| leg.leg_id == spec.leader_leg_id)
            .ok_or_else(|| ExecutionError::Invalid("unwind leader leg is missing".into()))?;
        let leader_order = leader_leg
            .order_ids
            .iter()
            .find_map(|order_id| self.actor.order_map().get(order_id.as_str()))
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unwind leader order is missing".into()))?;
        let reference_price = self
            .actor
            .fills()
            .iter()
            .filter(|fill| leader_leg.order_ids.iter().any(|id| id == &fill.order_id))
            .max_by_key(|fill| fill.occurred_at_unix_nanos)
            .map(|fill| fill.price)
            .ok_or_else(|| ExecutionError::Invalid("unwind reference fill is missing".into()))?;
        let unwind_side = match leader_order.side {
            OrderSide::Buy => OrderSide::Sell,
            OrderSide::Sell => OrderSide::Buy,
        };
        let limit_price = protected_unwind_price(reference_price, unwind_side, max_slippage_bps)?;
        let unwind_quantity = spec
            .leader_quantity_for_hedge_exposure(exposure.unhedged_after_commitment)
            .map_err(ExecutionError::Invalid)?;
        let order_id = OrderId::new(format!(
            "{}:unwind:decision:{}",
            intent_id,
            run.decision_sequence.get().saturating_add(1)
        ))
        .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
        let decision_time = run
            .last_decision_at
            .map(|last| last.get().max(business_time_unix_nanos))
            .unwrap_or(business_time_unix_nanos);
        let decision = decide_maker_taker_hedge(
            &run,
            AlgorithmInput {
                business_time: decision_time.into(),
                ready_children: vec![AlgorithmChildCandidate {
                    order_id: order_id.clone(),
                    leg_id: spec.leader_leg_id.clone(),
                    quantity: unwind_quantity,
                    execution_style: AlgorithmExecutionStyle::UnwindImmediate,
                    execution_route_id: leader_order.execution_route_id.clone(),
                }],
            },
        )
        .map_err(ExecutionError::Invalid)?;
        self.actor
            .apply_algorithm_decision(intent_id, decision)
            .map_err(ExecutionError::Invalid)?
            .into_iter()
            .find(|action| {
                matches!(
                    &action.kind,
                    AlgorithmActionKind::SubmitChild {
                        order_id: candidate_order_id,
                        execution_style: AlgorithmExecutionStyle::UnwindImmediate,
                        ..
                    } if candidate_order_id == &order_id
                )
            })
            .ok_or_else(|| {
                ExecutionError::Invalid("maker-taker algorithm did not authorize unwind".into())
            })?;
        let mut options = template_options(&state.intent, spec.leader_leg_id.as_str());
        options.time_in_force = Some("IOC".into());
        options.post_only = Some(false);
        options.split = None;
        options.maker = None;
        let request = SubmitOrder {
            order_id,
            intent_id: Some(state.intent.intent_id.clone()),
            strategy_id: Some(state.intent.strategy_id.clone()),
            account_id: leader_order.account_id.clone(),
            segment_key: leader_order.segment_key.clone(),
            instrument_id: leader_order.instrument_id.clone(),
            market_id: leader_order.market_id.clone(),
            execution_route_id: leader_order.execution_route_id.clone(),
            side: unwind_side,
            order_type: OrderType::Limit,
            quantity: unwind_quantity,
            limit_price: Some(limit_price),
            options,
            submitted_at_unix_nanos: Some(decision_time.into()),
        };
        self.actor.increment_compensation_attempts(intent_id);
        self.actor
            .schedule_pending_order(intent_id, request.clone(), decision_time.into())
            .map_err(ExecutionError::Invalid)?;
        // Persist the stable action and its reconstructable request atomically.
        // If the process stops before dispatch, the ordinary due-order loop
        // resumes this exact order identity and execution style after restart.
        self.persist_snapshot()?;
        let current = self
            .actor
            .intent(intent_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unwind intent disappeared".into()))?;
        self.commit_intent(IntentEvent {
            intent_id: typed_intent_id(intent_id),
            strategy_decision_id: None,
            event_sequence: 0.into(),
            previous_status: None,
            status: IntentStatus::Compensating,
            order_ids: Vec::new(),
            completed_quantity: current.completed_quantity,
            occurred_at_unix_nanos: decision_time.into(),
            reason: format!("emergency unwind scheduled: {unwind_reason}"),
            dependency_watermarks: current.dependency_watermarks,
        })?;
        Ok(true)
    }

    pub(crate) fn prepare_compensating_hedge(
        &mut self,
        intent_id: &str,
    ) -> Result<Option<PreparedCompensatingHedge>, ExecutionError> {
        let Some(state) = self.actor.intent(intent_id).cloned() else {
            return Ok(None);
        };
        let Some(policy) = state.intent.algorithm.hedge_policy().cloned() else {
            return Ok(None);
        };
        let Some(plan) = state.plan.clone() else {
            return Ok(None);
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
            return Ok(None);
        }
        let business_time = self.require_business_time("compensating hedge preparation")?;
        if state.compensation_attempts >= policy.max_compensation_attempts {
            self.commit_intent(IntentEvent {
                intent_id: typed_intent_id(intent_id),
                strategy_decision_id: None,
                event_sequence: 0.into(),
                previous_status: None,
                status: IntentStatus::ReconciliationRequired,
                order_ids: Vec::new(),
                completed_quantity: state.completed_quantity,
                occurred_at_unix_nanos: business_time.into(),
                reason: format!(
                    "compensation breaker opened after {} attempts; unhedged={missing}",
                    state.compensation_attempts
                ),
                dependency_watermarks: state.dependency_watermarks,
            })?;
            return Ok(None);
        }
        if !policy.compensate_on_failure {
            let current = self
                .actor
                .intent(intent_id)
                .cloned()
                .ok_or_else(|| ExecutionError::Invalid("hedge intent disappeared".into()))?;
            self.commit_intent(IntentEvent {
                intent_id: typed_intent_id(intent_id),
                strategy_decision_id: None,
                event_sequence: 0.into(),
                previous_status: None,
                status: IntentStatus::ReconciliationRequired,
                order_ids: Vec::new(),
                completed_quantity: current.completed_quantity,
                occurred_at_unix_nanos: business_time.into(),
                reason: "hedge exposure exceeds tolerance and compensation is disabled".into(),
                dependency_watermarks: current.dependency_watermarks,
            })?;
            return Ok(None);
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
            return Ok(None);
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
            strategy_id: Some(state.intent.strategy_id.clone()),
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
            submitted_at_unix_nanos: Some(business_time.into()),
        };
        self.actor.increment_compensation_attempts(intent_id);
        Ok(Some(PreparedCompensatingHedge {
            intent_id: typed_intent_id(intent_id),
            leg_id: policy.hedge_leg_id,
            request,
        }))
    }

    pub(crate) fn complete_compensating_hedge(
        &mut self,
        prepared: &PreparedCompensatingHedge,
        order: &ExecutionOrder,
    ) -> Result<(), ExecutionError> {
        let business_time = self.require_business_time("compensating hedge completion")?;
        self.attach_plan_order(
            prepared.intent_id.as_str(),
            &prepared.leg_id,
            &order.order_id,
        )?;
        let state = self
            .actor
            .intent(prepared.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("hedge intent disappeared".into()))?;
        self.commit_intent(IntentEvent {
            intent_id: prepared.intent_id.clone(),
            strategy_decision_id: None,
            event_sequence: 0.into(),
            previous_status: None,
            status: IntentStatus::Compensating,
            order_ids: vec![order.order_id.clone()],
            completed_quantity: state.completed_quantity,
            occurred_at_unix_nanos: business_time.into(),
            reason: "leader fill exceeded active hedge quantity".into(),
            dependency_watermarks: state.dependency_watermarks,
        })
    }

    pub(crate) fn fail_compensating_hedge(
        &mut self,
        prepared: &PreparedCompensatingHedge,
        error: &ExecutionError,
    ) -> Result<(), ExecutionError> {
        let business_time = self.require_business_time("compensating hedge failure")?;
        let state = self
            .actor
            .intent(prepared.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("hedge intent disappeared".into()))?;
        self.commit_intent(IntentEvent {
            intent_id: prepared.intent_id.clone(),
            strategy_decision_id: None,
            event_sequence: 0.into(),
            previous_status: None,
            status: IntentStatus::ReconciliationRequired,
            order_ids: Vec::new(),
            completed_quantity: state.completed_quantity,
            occurred_at_unix_nanos: business_time.into(),
            reason: format!("compensating hedge failed: {error}"),
            dependency_watermarks: state.dependency_watermarks,
        })
    }

    pub fn drain_intent_events(&mut self) -> Vec<IntentEvent> {
        self.actor.drain_intent_events()
    }
}

fn next_fallback_route_id(
    run: &AlgorithmRun,
    spec: &MakerTakerHedgeSpec,
) -> Option<ExecutionRouteId> {
    let attempted = run
        .actions
        .iter()
        .filter_map(|action| match &action.kind {
            AlgorithmActionKind::SubmitChild {
                execution_style: AlgorithmExecutionStyle::TakerImmediate,
                execution_route_id,
                ..
            } => execution_route_id.clone(),
            _ => None,
        })
        .collect::<BTreeSet<_>>();
    spec.fallback_execution_route_ids
        .iter()
        .find(|route_id| !attempted.contains(*route_id))
        .cloned()
}

fn protected_unwind_price(
    reference_price: Price,
    unwind_side: OrderSide,
    max_slippage_bps: u32,
) -> Result<Price, ExecutionError> {
    const BASIS_POINTS: i128 = 10_000;

    if reference_price.mantissa() <= 0 {
        return Err(ExecutionError::Invalid(
            "unwind reference price must be positive".into(),
        ));
    }
    if max_slippage_bps >= BASIS_POINTS as u32 {
        return Err(ExecutionError::Invalid(
            "unwind maximum slippage must be below 10,000 bps".into(),
        ));
    }

    let reference = i128::from(reference_price.mantissa());
    let adjustment = i128::from(max_slippage_bps);
    let protected = match unwind_side {
        OrderSide::Sell => {
            reference
                .checked_mul(BASIS_POINTS - adjustment)
                .ok_or_else(|| ExecutionError::Invalid("unwind price overflow".into()))?
                / BASIS_POINTS
        },
        OrderSide::Buy => {
            let numerator = reference
                .checked_mul(BASIS_POINTS + adjustment)
                .ok_or_else(|| ExecutionError::Invalid("unwind price overflow".into()))?;
            numerator
                .checked_add(BASIS_POINTS - 1)
                .ok_or_else(|| ExecutionError::Invalid("unwind price overflow".into()))?
                / BASIS_POINTS
        },
    };
    let protected = i64::try_from(protected)
        .map_err(|_| ExecutionError::Invalid("unwind price exceeds supported range".into()))?;
    if protected <= 0 {
        return Err(ExecutionError::Invalid(
            "unwind price protection rounded below the minimum positive price".into(),
        ));
    }
    Price::new(protected, reference_price.scale())
        .map_err(|error| ExecutionError::Invalid(error.to_string()))
}
