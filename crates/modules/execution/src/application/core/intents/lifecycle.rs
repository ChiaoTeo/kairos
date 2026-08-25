//! Scheduled order advancement, cancellation, and expiry.

use std::collections::BTreeSet;

use super::super::*;
use crate::domain::AlgorithmRunStatus;

pub(crate) struct DueIntentOrder {
    pub(crate) intent_id: IntentId,
    pub(crate) leg_id: String,
    pub(crate) action_id: String,
    pub(crate) execution_style: AlgorithmExecutionStyle,
    pub(crate) request: SubmitOrder,
}

impl ExecutionApplication {
    pub(in crate::application) fn due_algorithm_intents(
        &self,
        now_unix_nanos: u64,
        limit: usize,
    ) -> Vec<String> {
        self.actor
            .algorithm_runs()
            .filter(|run| {
                matches!(
                    run.spec,
                    ExecutionAlgorithmSpec::MakerTakerHedge(_) | ExecutionAlgorithmSpec::Twap(_)
                ) && run
                    .next_wake_at
                    .is_some_and(|wake| wake.get() <= now_unix_nanos)
                    && !matches!(
                        run.status,
                        AlgorithmRunStatus::Completed
                            | AlgorithmRunStatus::Unwound
                            | AlgorithmRunStatus::Failed
                            | AlgorithmRunStatus::ReconciliationRequired
                    )
            })
            .take(limit)
            .map(|run| run.intent_id.to_string())
            .collect()
    }

    pub fn advance_due_algorithm_runs(
        &mut self,
        now_unix_nanos: u64,
        limit: usize,
    ) -> Result<usize, ExecutionError> {
        let due = self.prepare_due_algorithm_runs(now_unix_nanos, limit)?;
        if !due.is_empty() {
            self.advance_due_intent_orders(now_unix_nanos, usize::MAX)?;
        }
        Ok(due.len())
    }

    /// Perform the provider-neutral preparation required by due algorithms.
    /// Direct and managed runtimes share this method and differ only in how
    /// the resulting durable due orders reach Integration.
    pub(crate) fn prepare_due_algorithm_runs(
        &mut self,
        now_unix_nanos: u64,
        limit: usize,
    ) -> Result<Vec<String>, ExecutionError> {
        let due = self.due_algorithm_intents(now_unix_nanos, limit);
        for intent_id in &due {
            if self
                .actor
                .algorithm_run(intent_id)
                .is_some_and(|run| matches!(run.spec, ExecutionAlgorithmSpec::MakerTakerHedge(_)))
            {
                self.drive_maker_taker_hedge(intent_id, now_unix_nanos)?;
            }
        }
        Ok(due)
    }

    /// Submit due child orders from durable Intent scheduling state.  The
    /// state loop calls this frequently; command submission therefore never
    /// blocks on maker cadence or an algorithm-owned wake time.
    pub fn advance_due_intent_orders(
        &mut self,
        now_unix_nanos: u64,
        limit: usize,
    ) -> Result<usize, ExecutionError> {
        let mut submitted = 0;
        while submitted < limit {
            let Some(due) = self.take_due_intent_order(now_unix_nanos)? else {
                break;
            };
            match self.submit(due.request.clone()) {
                Ok(order) => {
                    self.complete_due_intent_order(&due, &order, now_unix_nanos)?;
                    submitted += 1;
                },
                Err(error) => {
                    let cancellations = self.fail_due_intent_order(&due, &error, now_unix_nanos)?;
                    for cancellation in cancellations {
                        let _ = self.cancel(cancellation);
                    }
                    if due.execution_style == AlgorithmExecutionStyle::TakerImmediate
                        && !matches!(error, ExecutionError::Indeterminate(_))
                        && self
                            .actor
                            .intent(due.intent_id.as_str())
                            .is_some_and(|state| !state.pending_orders.is_empty())
                    {
                        continue;
                    }
                    if matches!(
                        due.execution_style,
                        AlgorithmExecutionStyle::TakerImmediate
                            | AlgorithmExecutionStyle::UnwindImmediate
                    ) {
                        break;
                    }
                    return Err(error);
                },
            }
        }
        if submitted > 0 {
            self.persist_snapshot()?;
        }
        Ok(submitted)
    }

    pub(crate) fn take_due_intent_order(
        &mut self,
        now_unix_nanos: u64,
    ) -> Result<Option<DueIntentOrder>, ExecutionError> {
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
            return Ok(None);
        };
        let leg_id = self
            .actor
            .algorithm_run(intent_id.as_str())
            .and_then(|run| {
                run.pending_actions().find_map(|action| match &action.kind {
                    AlgorithmActionKind::SubmitChild {
                        order_id, leg_id, ..
                    } if order_id == &request.order_id => Some(leg_id.to_string()),
                    _ => None,
                })
            })
            .unwrap_or_else(|| {
                intent_leg_id(
                    &self
                        .actor
                        .intent(intent_id.as_str())
                        .expect("scheduled intent was selected from actor state")
                        .intent,
                    &request,
                )
            });
        let (action_id, execution_style) =
            self.ensure_due_intent_action(intent_id.as_str(), &leg_id, &request, now_unix_nanos)?;
        self.actor
            .remove_pending_order(intent_id.as_str(), &request.order_id);
        Ok(Some(DueIntentOrder {
            intent_id,
            leg_id,
            action_id,
            execution_style,
            request,
        }))
    }

    fn ensure_due_intent_action(
        &mut self,
        intent_id: &str,
        leg_id: &str,
        request: &SubmitOrder,
        business_time_unix_nanos: u64,
    ) -> Result<(String, AlgorithmExecutionStyle), ExecutionError> {
        if let Some(action) = self.actor.algorithm_run(intent_id).and_then(|run| {
            run.pending_actions().find_map(|action| match &action.kind {
                AlgorithmActionKind::SubmitChild {
                    order_id,
                    execution_style,
                    ..
                } if order_id == &request.order_id => {
                    Some((action.action_id.clone(), *execution_style))
                },
                _ => None,
            })
        }) {
            return Ok(action);
        }
        let run = self
            .actor
            .algorithm_run(intent_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("intent has no algorithm run".into()))?;
        let execution_style = match &run.spec {
            ExecutionAlgorithmSpec::Immediate => AlgorithmExecutionStyle::Immediate,
            ExecutionAlgorithmSpec::Twap(_) => AlgorithmExecutionStyle::TwapSlice,
            ExecutionAlgorithmSpec::MakerTakerHedge(_) => AlgorithmExecutionStyle::MakerPostOnly,
        };
        let input = AlgorithmInput {
            business_time: business_time_unix_nanos.into(),
            ready_children: vec![AlgorithmChildCandidate {
                order_id: request.order_id.clone(),
                leg_id: LegId::new(leg_id.to_string())
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                quantity: request.quantity,
                execution_style,
                execution_route_id: request.execution_route_id.clone(),
            }],
        };
        let decision = match &run.spec {
            ExecutionAlgorithmSpec::Immediate => decide_immediate(&run, input),
            ExecutionAlgorithmSpec::Twap(_) => decide_twap(&run, input),
            ExecutionAlgorithmSpec::MakerTakerHedge(_) => decide_maker_taker_hedge(&run, input),
        }
        .map_err(ExecutionError::Invalid)?;
        let actions = self
            .actor
            .apply_algorithm_decision(intent_id, decision)
            .map_err(ExecutionError::Invalid)?;
        let action_id = actions
            .into_iter()
            .find_map(|action| match action.kind {
                AlgorithmActionKind::SubmitChild { order_id, .. }
                    if order_id == request.order_id =>
                {
                    Some(action.action_id)
                },
                _ => None,
            })
            .ok_or_else(|| {
                ExecutionError::Invalid(
                    "immediate algorithm did not authorize the due child".into(),
                )
            })?;
        // The decision and stable action identity must be durable before any
        // order-side work can remove the scheduled request or reach a venue.
        self.persist_snapshot()?;
        Ok((action_id, execution_style))
    }

    pub(crate) fn complete_due_intent_order(
        &mut self,
        due: &DueIntentOrder,
        order: &ExecutionOrder,
        now_unix_nanos: u64,
    ) -> Result<(), ExecutionError> {
        self.actor
            .set_algorithm_action_status(
                due.intent_id.as_str(),
                &due.action_id,
                AlgorithmActionStatus::Completed,
            )
            .map_err(ExecutionError::Invalid)?;
        if due.execution_style == AlgorithmExecutionStyle::UnwindImmediate {
            self.actor
                .attach_intent_algorithm_order(
                    due.intent_id.as_str(),
                    LegId::new(due.leg_id.clone())
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                    order.order_id.as_str(),
                )
                .map_err(ExecutionError::Invalid)?;
        } else {
            self.attach_plan_order(&due.intent_id, &due.leg_id, &order.order_id)?;
        }
        let state = self
            .actor
            .intent(due.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("scheduled intent disappeared".into()))?;
        self.commit_intent(IntentEvent {
            intent_id: due.intent_id.clone(),
            strategy_decision_id: None,
            event_sequence: 0.into(),
            previous_status: None,
            status: if matches!(
                due.execution_style,
                AlgorithmExecutionStyle::TakerImmediate | AlgorithmExecutionStyle::UnwindImmediate
            ) {
                IntentStatus::Compensating
            } else {
                IntentStatus::Executing
            },
            order_ids: vec![order.order_id.clone()],
            completed_quantity: state.completed_quantity,
            occurred_at_unix_nanos: now_unix_nanos.into(),
            reason: match due.execution_style {
                AlgorithmExecutionStyle::TakerImmediate => {
                    "leader fill triggered taker hedge".into()
                },
                AlgorithmExecutionStyle::TwapSlice => "TWAP slice created".into(),
                AlgorithmExecutionStyle::UnwindImmediate => "emergency unwind order created".into(),
                _ => "child order created".into(),
            },
            dependency_watermarks: state.dependency_watermarks,
        })?;
        self.refresh_intent(due.intent_id.as_str())
    }

    pub(crate) fn fail_due_intent_order(
        &mut self,
        due: &DueIntentOrder,
        error: &ExecutionError,
        now_unix_nanos: u64,
    ) -> Result<Vec<CancelOrder>, ExecutionError> {
        warn!(event = "scheduled_child_order_failed", component = "execution", intent_id = %due.intent_id, error = %error, "scheduled child order failed");
        self.actor
            .set_algorithm_action_status(
                due.intent_id.as_str(),
                &due.action_id,
                if matches!(error, ExecutionError::Indeterminate(_)) {
                    AlgorithmActionStatus::Indeterminate
                } else {
                    AlgorithmActionStatus::Failed
                },
            )
            .map_err(ExecutionError::Invalid)?;
        if self
            .actor
            .order_map()
            .contains_key(due.request.order_id.as_str())
        {
            if due.execution_style == AlgorithmExecutionStyle::UnwindImmediate {
                self.actor
                    .attach_intent_algorithm_order(
                        due.intent_id.as_str(),
                        LegId::new(due.leg_id.clone())
                            .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                        due.request.order_id.as_str(),
                    )
                    .map_err(ExecutionError::Invalid)?;
            } else {
                self.attach_plan_order(&due.intent_id, &due.leg_id, &due.request.order_id)?;
            }
            let orders = self
                .actor
                .order_map()
                .values()
                .filter(|order| order.intent_id.as_ref() == Some(&due.intent_id))
                .cloned()
                .collect::<Vec<_>>();
            self.actor
                .synchronize_algorithm_run(due.intent_id.as_str(), &orders, false)
                .map_err(ExecutionError::Invalid)?;
        }
        if due.execution_style == AlgorithmExecutionStyle::TakerImmediate
            && !matches!(error, ExecutionError::Indeterminate(_))
        {
            self.drive_maker_taker_hedge(due.intent_id.as_str(), now_unix_nanos)?;
            return Ok(Vec::new());
        }
        let state = self.actor.intent(due.intent_id.as_str());
        let cancel_siblings = !matches!(
            due.execution_style,
            AlgorithmExecutionStyle::TakerImmediate | AlgorithmExecutionStyle::UnwindImmediate
        ) && state.is_none_or(|state| {
            matches!(state.intent.failure_policy, FailurePolicy::CancelRemaining)
                || state.intent.intent_type == IntentType::PairArbitrage
        });
        let cancellations = if cancel_siblings {
            state
                .map(|state| state.order_ids.clone())
                .unwrap_or_default()
                .into_iter()
                .filter(|order_id| {
                    self.actor
                        .order_map()
                        .get(order_id.as_str())
                        .is_some_and(|order| !order.status.terminal())
                })
                .map(|order_id| CancelOrder {
                    order_id,
                    reason: "pair leg submission failed".into(),
                })
                .collect()
        } else {
            Vec::new()
        };
        let completed_quantity = state
            .map(|state| state.completed_quantity)
            .unwrap_or_default();
        self.commit_intent(IntentEvent {
            intent_id: due.intent_id.clone(),
            strategy_decision_id: None,
            event_sequence: 0.into(),
            previous_status: None,
            status: if matches!(
                due.execution_style,
                AlgorithmExecutionStyle::TakerImmediate | AlgorithmExecutionStyle::UnwindImmediate
            ) || matches!(error, ExecutionError::Indeterminate(_))
            {
                IntentStatus::ReconciliationRequired
            } else {
                IntentStatus::Failed
            },
            order_ids: Vec::new(),
            completed_quantity,
            occurred_at_unix_nanos: now_unix_nanos.into(),
            reason: error.to_string(),
            dependency_watermarks: self.dependency_watermarks(),
        })?;
        Ok(cancellations)
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
            strategy_decision_id: None,
            event_sequence: 0.into(),
            previous_status: None,
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
                strategy_decision_id: None,
                event_sequence: 0.into(),
                previous_status: None,
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
        let cancellations = self.begin_intent_expiration(&request)?;
        for cancellation in cancellations {
            let _ = self.cancel(cancellation);
        }
        self.complete_intent_expiration(&request)
    }

    pub(crate) fn begin_intent_expiration(
        &mut self,
        request: &ExpireIntent,
    ) -> Result<Vec<CancelOrder>, ExecutionError> {
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
            strategy_decision_id: None,
            event_sequence: 0.into(),
            previous_status: None,
            status: IntentStatus::Expired,
            order_ids: state.order_ids.clone(),
            completed_quantity: state.completed_quantity,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: if request.reason.trim().is_empty() {
                "intent expired".into()
            } else {
                request.reason.clone()
            },
            dependency_watermarks: state.dependency_watermarks,
        })?;
        Ok(state
            .order_ids
            .into_iter()
            .filter(|order_id| {
                self.actor
                    .order_map()
                    .get(order_id.as_str())
                    .is_some_and(|order| !order.status.terminal())
            })
            .map(|order_id| CancelOrder {
                order_id,
                reason: "parent intent expired".into(),
            })
            .collect())
    }

    pub(crate) fn complete_intent_expiration(
        &mut self,
        request: &ExpireIntent,
    ) -> Result<IntentState, ExecutionError> {
        // Child cancellation refreshes the parent intent. Re-assert the
        // deadline outcome after those child lifecycle events so expiration
        // remains the authoritative terminal reason.
        self.commit_intent(IntentEvent {
            intent_id: request.intent_id.clone(),
            strategy_decision_id: None,
            event_sequence: 0.into(),
            previous_status: None,
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

    pub(crate) fn due_intent_expirations(&self, now_unix_nanos: u64) -> Vec<ExpireIntent> {
        self.actor
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
            .map(|state| ExpireIntent {
                intent_id: state.intent.intent_id.clone(),
                reason: "intent deadline reached".into(),
            })
            .collect()
    }

    pub fn expire_due_intents(&mut self, now_unix_nanos: u64) -> Result<usize, ExecutionError> {
        let due = self.due_intent_expirations(now_unix_nanos);
        let count = due.len();
        for request in due {
            self.expire_intent(request)?;
        }
        Ok(count)
    }

    pub(crate) fn commit_intent(&mut self, event: IntentEvent) -> Result<(), ExecutionError> {
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
                changes: vec![ExecutionBusinessChange::Intent { state, event }],
            });
        Ok(())
    }

    pub(in crate::application) fn persist_snapshot(&mut self) -> Result<(), ExecutionError> {
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            store.save(&snapshot).map_err(ExecutionError::Persistence)?;
        }
        Ok(())
    }

    pub(crate) fn refresh_intent(&mut self, intent_id: &str) -> Result<(), ExecutionError> {
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
        let unwind_order_ids = self
            .actor
            .algorithm_run(intent_id)
            .into_iter()
            .flat_map(|run| &run.actions)
            .filter_map(|action| match &action.kind {
                AlgorithmActionKind::SubmitChild {
                    order_id,
                    execution_style: AlgorithmExecutionStyle::UnwindImmediate,
                    ..
                } => Some(order_id.clone()),
                _ => None,
            })
            .collect::<BTreeSet<_>>();
        let completed = orders
            .iter()
            .filter(|order| !unwind_order_ids.contains(&order.order_id))
            .try_fold(Quantity::ZERO, |total, order| {
                total.checked_add(order.filled_quantity)
            })
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
        self.refresh_plan_progress(intent_id, &orders)?;
        let algorithm_changed = self
            .actor
            .synchronize_algorithm_run(intent_id, &orders, !state.pending_orders.is_empty())
            .map_err(ExecutionError::Invalid)?;
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
        let algorithm_status = self.actor.algorithm_run(intent_id).map(|run| run.status);
        let unwind_active = self
            .actor
            .algorithm_run(intent_id)
            .and_then(|run| run.exposure.as_ref())
            .is_some_and(|exposure| !exposure.unwind_committed_quantity.is_zero());
        let unwind_recovery_required = self.actor.algorithm_run(intent_id).is_some_and(|run| {
            let ExecutionAlgorithmSpec::MakerTakerHedge(spec) = &run.spec else {
                return false;
            };
            run.legs.iter().any(|leg| {
                leg.leg_id == spec.hedge_leg_id
                    && leg.lifecycle == crate::domain::AlgorithmLegLifecycle::Failed
            }) && run.exposure.as_ref().is_some_and(|exposure| {
                exposure.unhedged_after_commitment > spec.max_unhedged_quantity
            })
        });
        let status = if algorithm_status == Some(AlgorithmRunStatus::Unwound) {
            IntentStatus::Failed
        } else if unwind_active || unwind_recovery_required {
            IntentStatus::Compensating
        } else if (all_filled || policy_satisfied) && !has_pending && !has_failed {
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
                },
                crate::domain::IntentLifecycle::Failed => IntentStatus::Failed,
                crate::domain::IntentLifecycle::Canceled => IntentStatus::Canceled,
                _ if has_active => {
                    if completed > Quantity::ZERO {
                        IntentStatus::PartiallyFilled
                    } else {
                        IntentStatus::Executing
                    }
                },
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
            if algorithm_changed {
                self.persist_snapshot()?;
            }
            return Ok(());
        }
        self.commit_intent(IntentEvent {
            intent_id: typed_intent_id(intent_id),
            strategy_decision_id: None,
            event_sequence: 0.into(),
            previous_status: None,
            status,
            order_ids: state.order_ids,
            completed_quantity: completed,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: match status {
                IntentStatus::Satisfied => "all child orders filled".into(),
                IntentStatus::PartiallyFilled => "child orders partially filled".into(),
                IntentStatus::Canceled => "all child orders canceled".into(),
                IntentStatus::Failed if algorithm_status == Some(AlgorithmRunStatus::Unwound) => {
                    "leader exposure was closed by emergency unwind".into()
                },
                IntentStatus::Failed => "all child orders failed".into(),
                IntentStatus::ReconciliationRequired => {
                    "child order reconciliation required".into()
                },
                IntentStatus::Compensating => "emergency unwind is active".into(),
                _ => "child execution progressing".into(),
            },
            dependency_watermarks: state.dependency_watermarks,
        })
    }

    pub(crate) fn attach_plan_order(
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
