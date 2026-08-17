//! Scheduled order advancement, cancellation, and expiry.

use super::super::*;

impl ExecutionApplication {
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
                changes: vec![ExecutionBusinessChange::Intent(state)],
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
