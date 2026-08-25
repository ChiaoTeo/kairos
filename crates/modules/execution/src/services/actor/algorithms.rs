use super::*;
use crate::domain::{
    AlgorithmAction, AlgorithmActionKind, AlgorithmActionStatus, AlgorithmDecision,
    AlgorithmExecutionStyle, AlgorithmLegLifecycle, AlgorithmRun, AlgorithmRunStatus,
    DeliveryCertainty, ExecutionAlgorithmSpec, ExecutionOrderStatus,
};

impl ExecutionActor {
    pub(crate) fn restore_algorithm_runs(&mut self, runs: Vec<AlgorithmRun>) -> Result<(), String> {
        let mut restored = BTreeMap::new();
        for run in runs {
            run.validate()?;
            if !self.intents.contains_key(run.intent_id.as_str()) {
                return Err(format!(
                    "algorithm run {} references missing intent {}",
                    run.algorithm_run_id, run.intent_id
                ));
            }
            if restored.insert(run.intent_id.to_string(), run).is_some() {
                return Err("multiple algorithm runs reference the same intent".into());
            }
        }
        self.algorithm_runs = restored;
        Ok(())
    }

    pub(crate) fn insert_algorithm_run(&mut self, run: AlgorithmRun) -> Result<(), String> {
        run.validate()?;
        if !self.intents.contains_key(run.intent_id.as_str()) {
            return Err("algorithm run owner intent is missing".into());
        }
        if self.algorithm_runs.contains_key(run.intent_id.as_str()) {
            return Err("intent already has an algorithm run".into());
        }
        self.algorithm_runs.insert(run.intent_id.to_string(), run);
        self.generation = self.generation.saturating_add(1);
        Ok(())
    }

    pub(crate) fn algorithm_runs(&self) -> impl Iterator<Item = &AlgorithmRun> {
        self.algorithm_runs.values()
    }

    pub(crate) fn algorithm_run(&self, intent_id: &str) -> Option<&AlgorithmRun> {
        self.algorithm_runs.get(intent_id)
    }

    pub(crate) fn apply_algorithm_decision(
        &mut self,
        intent_id: &str,
        decision: AlgorithmDecision,
    ) -> Result<Vec<AlgorithmAction>, String> {
        let run = self
            .algorithm_runs
            .get_mut(intent_id)
            .ok_or_else(|| "intent has no algorithm run".to_string())?;
        let previous_actions = run.actions.len();
        run.apply_decision(decision)?;
        let actions = run.actions[previous_actions..].to_vec();
        self.generation = self.generation.saturating_add(1);
        Ok(actions)
    }

    pub(crate) fn set_algorithm_action_status(
        &mut self,
        intent_id: &str,
        action_id: &str,
        status: AlgorithmActionStatus,
    ) -> Result<(), String> {
        let run = self
            .algorithm_runs
            .get_mut(intent_id)
            .ok_or_else(|| "intent has no algorithm run".to_string())?;
        run.set_action_status(action_id, status)?;
        self.generation = self.generation.saturating_add(1);
        Ok(())
    }

    pub(crate) fn synchronize_all_algorithm_runs(&mut self) -> Result<bool, String> {
        let intent_ids = self.algorithm_runs.keys().cloned().collect::<Vec<_>>();
        let mut changed = false;
        for intent_id in intent_ids {
            let action_orders = self
                .algorithm_runs
                .get(&intent_id)
                .into_iter()
                .flat_map(|run| &run.actions)
                .filter_map(|action| match &action.kind {
                    AlgorithmActionKind::SubmitChild {
                        order_id,
                        leg_id,
                        execution_style,
                        ..
                    } if self.orders.contains_key(order_id.as_str()) => {
                        Some((*execution_style, leg_id.clone(), order_id.clone()))
                    },
                    _ => None,
                })
                .collect::<Vec<_>>();
            for (execution_style, leg_id, order_id) in action_orders {
                let needs_recovery = self
                    .orders
                    .get(order_id.as_str())
                    .is_some_and(|order| order.leg_id.as_ref() != Some(&leg_id))
                    || self.intents.get(&intent_id).is_some_and(|state| {
                        !state.order_ids.iter().any(|value| value == &order_id)
                    });
                if execution_style == AlgorithmExecutionStyle::UnwindImmediate {
                    self.attach_intent_algorithm_order(&intent_id, leg_id, order_id.as_str())?;
                } else {
                    self.attach_intent_plan_order(&intent_id, leg_id.as_str(), order_id.as_str())?;
                }
                changed |= needs_recovery;
            }
            let orders = self
                .orders
                .values()
                .filter(|order| {
                    order
                        .intent_id
                        .as_ref()
                        .is_some_and(|id| id.as_str() == intent_id)
                })
                .cloned()
                .collect::<Vec<_>>();
            let has_pending_orders = self
                .intents
                .get(&intent_id)
                .is_some_and(|state| !state.pending_orders.is_empty());
            changed |= self.synchronize_algorithm_run(&intent_id, &orders, has_pending_orders)?;
        }
        Ok(changed)
    }

    pub(crate) fn synchronize_algorithm_run(
        &mut self,
        intent_id: &str,
        orders: &[ExecutionOrder],
        has_pending_orders: bool,
    ) -> Result<bool, String> {
        let leader_leg_id = self
            .algorithm_runs
            .get(intent_id)
            .and_then(|run| match &run.spec {
                ExecutionAlgorithmSpec::MakerTakerHedge(spec) => Some(spec.leader_leg_id.clone()),
                ExecutionAlgorithmSpec::Immediate => None,
            });
        let leader_order_ids = leader_leg_id
            .as_ref()
            .map(|leg_id| {
                orders
                    .iter()
                    .filter(|order| order.leg_id.as_ref() == Some(leg_id))
                    .map(|order| order.order_id.clone())
                    .collect::<BTreeSet<_>>()
            })
            .unwrap_or_default();
        let exposure_observed_at = self
            .fills
            .iter()
            .filter(|fill| leader_order_ids.contains(&fill.order_id))
            .map(|fill| fill.occurred_at_unix_nanos)
            .max();
        let Some(run) = self.algorithm_runs.get_mut(intent_id) else {
            return Ok(false);
        };
        let before = run.clone();
        let unwind_order_ids = run
            .actions
            .iter()
            .filter_map(|action| match &action.kind {
                AlgorithmActionKind::SubmitChild {
                    order_id,
                    execution_style: AlgorithmExecutionStyle::UnwindImmediate,
                    ..
                } => Some(order_id.clone()),
                _ => None,
            })
            .collect::<BTreeSet<_>>();
        for action in &mut run.actions {
            if action.status != AlgorithmActionStatus::Pending {
                continue;
            }
            let AlgorithmActionKind::SubmitChild { order_id, .. } = &action.kind else {
                continue;
            };
            let Some(order) = orders.iter().find(|order| &order.order_id == order_id) else {
                continue;
            };
            action.status = match order
                .attempts
                .last()
                .map(|attempt| attempt.delivery_certainty)
            {
                Some(DeliveryCertainty::Confirmed | DeliveryCertainty::Rejected) => {
                    AlgorithmActionStatus::Completed
                },
                Some(DeliveryCertainty::Indeterminate) => AlgorithmActionStatus::Indeterminate,
                Some(DeliveryCertainty::NotSent)
                    if matches!(
                        order.status,
                        ExecutionOrderStatus::Rejected | ExecutionOrderStatus::Failed
                    ) =>
                {
                    AlgorithmActionStatus::Failed
                },
                _ => AlgorithmActionStatus::Pending,
            };
        }
        for leg in &mut run.legs {
            let leg_orders = orders
                .iter()
                .filter(|order| {
                    order.leg_id.as_ref() == Some(&leg.leg_id)
                        && !unwind_order_ids.contains(&order.order_id)
                })
                .collect::<Vec<_>>();
            leg.filled_quantity = leg_orders.iter().try_fold(Quantity::ZERO, |total, order| {
                total.checked_add(order.filled_quantity)
            })?;
            leg.committed_quantity = leg_orders
                .iter()
                .filter(|order| {
                    matches!(
                        order.status,
                        ExecutionOrderStatus::Submitting
                            | ExecutionOrderStatus::Accepted
                            | ExecutionOrderStatus::PartiallyFilled
                            | ExecutionOrderStatus::CancelRequested
                            | ExecutionOrderStatus::Unknown
                    )
                })
                .try_fold(Quantity::ZERO, |total, order| {
                    let leaves = order.quantity.checked_sub(order.filled_quantity)?;
                    total.checked_add(leaves)
                })?;
            leg.lifecycle = if leg_orders.iter().any(|order| {
                order.status == ExecutionOrderStatus::Unknown
                    || order.attempts.last().is_some_and(|attempt| {
                        attempt.delivery_certainty == DeliveryCertainty::Indeterminate
                    })
            }) {
                AlgorithmLegLifecycle::ReconciliationRequired
            } else if leg.filled_quantity >= leg.target_quantity {
                AlgorithmLegLifecycle::Completed
            } else if !leg.committed_quantity.is_zero() {
                AlgorithmLegLifecycle::Active
            } else if !has_pending_orders
                && !leg_orders.is_empty()
                && leg_orders.iter().all(|order| {
                    matches!(
                        order.status,
                        ExecutionOrderStatus::Canceled
                            | ExecutionOrderStatus::Rejected
                            | ExecutionOrderStatus::Expired
                            | ExecutionOrderStatus::Failed
                    )
                })
            {
                AlgorithmLegLifecycle::Failed
            } else {
                AlgorithmLegLifecycle::Ready
            };
        }
        let unwind_orders = orders
            .iter()
            .filter(|order| unwind_order_ids.contains(&order.order_id))
            .collect::<Vec<_>>();
        let unwind_filled = unwind_orders
            .iter()
            .try_fold(Quantity::ZERO, |total, order| {
                total.checked_add(order.filled_quantity)
            })?;
        let unwind_committed = unwind_orders
            .iter()
            .filter(|order| {
                matches!(
                    order.status,
                    ExecutionOrderStatus::Submitting
                        | ExecutionOrderStatus::Accepted
                        | ExecutionOrderStatus::PartiallyFilled
                        | ExecutionOrderStatus::CancelRequested
                        | ExecutionOrderStatus::Unknown
                )
            })
            .try_fold(Quantity::ZERO, |total, order| {
                let leaves = order.quantity.checked_sub(order.filled_quantity)?;
                total.checked_add(leaves)
            })?;
        let unwind_requires_reconciliation = unwind_orders.iter().any(|order| {
            order.status == ExecutionOrderStatus::Unknown
                || order.attempts.last().is_some_and(|attempt| {
                    attempt.delivery_certainty == DeliveryCertainty::Indeterminate
                })
        });
        if matches!(run.spec, ExecutionAlgorithmSpec::MakerTakerHedge(_)) {
            run.synchronize_maker_taker_exposure(
                unwind_filled,
                unwind_committed,
                exposure_observed_at,
            )?;
        }
        let maker_exposure = run.exposure.as_ref();
        run.status = if unwind_requires_reconciliation
            || run
                .legs
                .iter()
                .any(|leg| leg.lifecycle == AlgorithmLegLifecycle::ReconciliationRequired)
        {
            AlgorithmRunStatus::ReconciliationRequired
        } else if maker_exposure.is_some_and(|exposure| {
            !exposure.unwind_filled_quantity.is_zero()
                && exposure.unhedged_filled_quantity
                    <= match &run.spec {
                        ExecutionAlgorithmSpec::MakerTakerHedge(spec) => spec.max_unhedged_quantity,
                        ExecutionAlgorithmSpec::Immediate => Quantity::ZERO,
                    }
                && exposure.hedge_committed_quantity.is_zero()
                && exposure.unwind_committed_quantity.is_zero()
        }) {
            AlgorithmRunStatus::Unwound
        } else if maker_exposure
            .is_some_and(|exposure| !exposure.unwind_committed_quantity.is_zero())
        {
            AlgorithmRunStatus::Running
        } else if matches!(run.spec, ExecutionAlgorithmSpec::MakerTakerHedge(_))
            && run
                .legs
                .iter()
                .any(|leg| leg.lifecycle == AlgorithmLegLifecycle::Failed)
            && maker_exposure.is_some_and(|exposure| !exposure.unhedged_after_commitment.is_zero())
        {
            AlgorithmRunStatus::Running
        } else if !run.legs.is_empty()
            && run
                .legs
                .iter()
                .all(|leg| leg.lifecycle == AlgorithmLegLifecycle::Completed)
        {
            AlgorithmRunStatus::Completed
        } else if run
            .legs
            .iter()
            .any(|leg| leg.lifecycle == AlgorithmLegLifecycle::Active)
        {
            AlgorithmRunStatus::Running
        } else if run
            .legs
            .iter()
            .any(|leg| leg.lifecycle == AlgorithmLegLifecycle::Failed)
        {
            AlgorithmRunStatus::Failed
        } else if run.next_wake_at.is_some() {
            AlgorithmRunStatus::Waiting
        } else {
            AlgorithmRunStatus::Planned
        };
        run.validate()?;
        let changed = *run != before;
        if changed {
            self.generation = self.generation.saturating_add(1);
        }
        Ok(changed)
    }
}
