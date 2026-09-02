use kairos_primitives::decimal::{Money, SignedQuantity};
use kairos_primitives::reference::Currency;
use kairos_primitives::time::DurationNanos;

use super::*;
use crate::domain::{
    AlgorithmAction, AlgorithmActionKind, AlgorithmActionStatus, AlgorithmDecision,
    AlgorithmExecutionQuality, AlgorithmExecutionStyle, AlgorithmLegBenchmarkQuality,
    AlgorithmLegExecutionQuality, AlgorithmLegLifecycle, AlgorithmRun, AlgorithmRunStatus,
    DeliveryCertainty, ExecutionAlgorithmSpec, ExecutionFeeTotal, ExecutionFill,
    ExecutionOrderStatus, OrderSide,
};

impl ExecutionActor {
    pub(crate) fn restore_algorithm_runs(&mut self, runs: Vec<AlgorithmRun>) -> Result<(), String> {
        let mut restored = BTreeMap::new();
        for run in runs {
            run.validate()?;
            self.validate_algorithm_run_owner(&run)?;
            if restored.insert(run.intent_id.to_string(), run).is_some() {
                return Err("multiple algorithm runs reference the same intent".into());
            }
        }
        self.algorithm_runs = restored;
        Ok(())
    }

    pub(crate) fn insert_algorithm_run(&mut self, run: AlgorithmRun) -> Result<(), String> {
        run.validate()?;
        self.validate_algorithm_run_owner(&run)?;
        if self.algorithm_runs.contains_key(run.intent_id.as_str()) {
            return Err("intent already has an algorithm run".into());
        }
        self.algorithm_runs.insert(run.intent_id.to_string(), run);
        self.generation = self.generation.saturating_add(1);
        Ok(())
    }

    fn validate_algorithm_run_owner(&self, run: &AlgorithmRun) -> Result<(), String> {
        let state = self.intents.get(run.intent_id.as_str()).ok_or_else(|| {
            format!(
                "algorithm run {} references missing intent {}",
                run.algorithm_run_id, run.intent_id
            )
        })?;
        for run_leg in &run.legs {
            let Some(benchmark) = run_leg.benchmark.as_ref() else {
                continue;
            };
            let plan_leg = state
                .plan
                .as_ref()
                .and_then(|plan| {
                    plan.legs
                        .iter()
                        .find(|plan_leg| plan_leg.leg_id == run_leg.leg_id)
                })
                .ok_or_else(|| {
                    format!(
                        "benchmarked algorithm leg {} has no owner plan leg",
                        run_leg.leg_id
                    )
                })?;
            if plan_leg.instrument_id != benchmark.instrument_id
                || plan_leg.market_id.as_ref() != Some(&benchmark.market_id)
            {
                return Err(format!(
                    "algorithm leg {} benchmark does not match its owner plan",
                    run_leg.leg_id
                ));
            }
        }
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
            let has_pending_work = self.intents.get(&intent_id).is_some_and(|state| {
                !state.pending_orders.is_empty() || state.pending_quote_refresh.is_some()
            });
            changed |= self.synchronize_algorithm_run(&intent_id, &orders, has_pending_work)?;
        }
        Ok(changed)
    }

    pub(crate) fn synchronize_algorithm_run(
        &mut self,
        intent_id: &str,
        orders: &[ExecutionOrder],
        has_pending_work: bool,
    ) -> Result<bool, String> {
        let leader_leg_id = self
            .algorithm_runs
            .get(intent_id)
            .and_then(|run| match &run.spec {
                ExecutionAlgorithmSpec::MakerTakerHedge(spec) => Some(spec.leader_leg_id.clone()),
                ExecutionAlgorithmSpec::Immediate
                | ExecutionAlgorithmSpec::Twap(_)
                | ExecutionAlgorithmSpec::PassiveLimit(_) => None,
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
        let quality = self
            .algorithm_runs
            .get(intent_id)
            .map(|run| realized_execution_quality(run, orders, &self.fills))
            .transpose()?;
        let Some(run) = self.algorithm_runs.get_mut(intent_id) else {
            return Ok(false);
        };
        let Some(quality) = quality else {
            return Ok(false);
        };
        let before = run.clone();
        run.quality = quality;
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
            if !matches!(
                action.status,
                AlgorithmActionStatus::Pending | AlgorithmActionStatus::Indeterminate
            ) {
                continue;
            }
            let AlgorithmActionKind::SubmitChild { order_id, .. } = &action.kind else {
                continue;
            };
            let Some(order) = orders.iter().find(|order| &order.order_id == order_id) else {
                continue;
            };
            action.status = if order.remote_order_id.is_some()
                && !matches!(
                    order.status,
                    ExecutionOrderStatus::Submitting | ExecutionOrderStatus::Unknown
                ) {
                // An authoritative venue event/query resolves the historical
                // uncertainty of the submit acknowledgement without rewriting
                // that attempt's original delivery evidence.
                AlgorithmActionStatus::Completed
            } else {
                match order
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
                }
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
            leg.lifecycle = if leg_orders
                .iter()
                .any(|order| order.status == ExecutionOrderStatus::Unknown)
            {
                AlgorithmLegLifecycle::ReconciliationRequired
            } else if leg.filled_quantity >= leg.target_quantity {
                AlgorithmLegLifecycle::Completed
            } else if !leg.committed_quantity.is_zero() {
                AlgorithmLegLifecycle::Active
            } else if !has_pending_work
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
        let unwind_requires_reconciliation = unwind_orders
            .iter()
            .any(|order| order.status == ExecutionOrderStatus::Unknown);
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
                        ExecutionAlgorithmSpec::Immediate
                        | ExecutionAlgorithmSpec::Twap(_)
                        | ExecutionAlgorithmSpec::PassiveLimit(_) => Quantity::ZERO,
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

fn realized_execution_quality(
    run: &AlgorithmRun,
    orders: &[ExecutionOrder],
    fills: &[ExecutionFill],
) -> Result<AlgorithmExecutionQuality, String> {
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
    let mut quality_legs = Vec::with_capacity(run.legs.len());
    for leg in &run.legs {
        let leg_orders = orders
            .iter()
            .filter(|order| {
                order.leg_id.as_ref() == Some(&leg.leg_id)
                    && !unwind_order_ids.contains(&order.order_id)
            })
            .collect::<Vec<_>>();
        let leg_order_ids = leg_orders
            .iter()
            .map(|order| order.order_id.clone())
            .collect::<BTreeSet<_>>();
        let leg_fills = fills
            .iter()
            .filter(|fill| leg_order_ids.contains(&fill.order_id))
            .collect::<Vec<_>>();
        let filled_quantity = leg_fills.iter().try_fold(Quantity::ZERO, |total, fill| {
            total.checked_add(fill.quantity)
        })?;
        let gross_notional = leg_fills.iter().try_fold(Money::ZERO, |total, fill| {
            total.checked_add(fill.quantity.checked_mul(fill.price)?)
        })?;
        let average_fill_price = if filled_quantity.is_zero() {
            None
        } else {
            Some(gross_notional.checked_div(SignedQuantity::new(
                filled_quantity.mantissa(),
                filled_quantity.scale(),
            )?)?)
        };
        let first_order_submitted_at = leg_orders
            .iter()
            .map(|order| order.submitted_at_unix_nanos)
            .min();
        let first_fill_at = leg_fills
            .iter()
            .map(|fill| fill.occurred_at_unix_nanos)
            .min();
        let last_fill_at = leg_fills
            .iter()
            .map(|fill| fill.occurred_at_unix_nanos)
            .max();
        let elapsed = |fill_at: Option<UnixNanos>| {
            first_order_submitted_at
                .zip(fill_at)
                .and_then(|(submitted, fill)| fill.get().checked_sub(submitted.get()))
                .map(DurationNanos::new)
        };
        let mut fee_totals = BTreeMap::<String, (Currency, Money)>::new();
        for fill in &leg_fills {
            let Some(currency) = fill.fee_currency.clone() else {
                continue;
            };
            let entry = fee_totals
                .entry(currency.to_string())
                .or_insert((currency, Money::ZERO));
            entry.1 = entry.1.checked_add(fill.fee)?;
        }
        let benchmark = leg
            .benchmark
            .as_ref()
            .map(|benchmark| {
                let benchmark_notional = filled_quantity.checked_mul(benchmark.price)?;
                let implementation_shortfall = if filled_quantity.is_zero() {
                    None
                } else {
                    let side = leg_orders
                        .first()
                        .map(|order| order.side)
                        .ok_or_else(|| "filled benchmark leg has no order".to_string())?;
                    if leg_orders.iter().any(|order| order.side != side) {
                        return Err("algorithm leg contains mixed order sides".to_string());
                    }
                    Some(match side {
                        OrderSide::Buy => gross_notional.checked_sub(benchmark_notional)?,
                        OrderSide::Sell => benchmark_notional.checked_sub(gross_notional)?,
                    })
                };
                Ok(AlgorithmLegBenchmarkQuality {
                    kind: benchmark.kind,
                    instrument_id: benchmark.instrument_id.clone(),
                    market_id: benchmark.market_id.clone(),
                    price: benchmark.price,
                    observed_at_unix_nanos: benchmark.observed_at_unix_nanos,
                    benchmark_notional,
                    implementation_shortfall,
                })
            })
            .transpose()?;
        quality_legs.push(AlgorithmLegExecutionQuality {
            leg_id: leg.leg_id.clone(),
            order_count: u64::try_from(leg_orders.len())
                .map_err(|_| "algorithm order count overflow".to_string())?,
            fill_count: u64::try_from(leg_fills.len())
                .map_err(|_| "algorithm fill count overflow".to_string())?,
            cancel_attempt_count: leg_orders.iter().try_fold(0_u64, |total, order| {
                let count = order
                    .attempts
                    .iter()
                    .filter(|attempt| {
                        attempt.command == crate::domain::ExecutionCommandKind::Cancel
                    })
                    .count();
                total
                    .checked_add(
                        u64::try_from(count)
                            .map_err(|_| "algorithm cancel count overflow".to_string())?,
                    )
                    .ok_or_else(|| "algorithm cancel count overflow".to_string())
            })?,
            filled_quantity,
            gross_notional,
            average_fill_price,
            first_order_submitted_at,
            first_fill_at,
            last_fill_at,
            time_to_first_fill: elapsed(first_fill_at),
            time_to_last_fill: elapsed(last_fill_at),
            fee_totals: fee_totals
                .into_values()
                .map(|(currency, amount)| ExecutionFeeTotal { currency, amount })
                .collect(),
            benchmark,
        });
    }
    Ok(AlgorithmExecutionQuality { legs: quality_legs })
}
