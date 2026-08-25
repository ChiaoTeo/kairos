use super::{
    AlgorithmActionKind, AlgorithmDecision, AlgorithmExecutionStyle, AlgorithmInput,
    AlgorithmLegLifecycle, AlgorithmRun, AlgorithmRunStatus, ExecutionAlgorithmSpec,
    NormalizedExposureLedger,
};
use kairos_primitives::decimal::Quantity;

impl AlgorithmRun {
    /// Rebuild the normalized exposure ledger from the run's per-leg filled
    /// and committed quantities. The ledger is expressed in hedge-leg units.
    pub fn synchronize_maker_taker_exposure(
        &mut self,
        unwind_filled_quantity: Quantity,
        unwind_committed_quantity: Quantity,
        exposure_observed_at: Option<kairos_primitives::time::UnixNanos>,
    ) -> Result<(), String> {
        let ExecutionAlgorithmSpec::MakerTakerHedge(spec) = self.spec.clone() else {
            return Err("exposure synchronization requires a maker-taker run".into());
        };
        let leader = self
            .legs
            .iter()
            .find(|leg| leg.leg_id == spec.leader_leg_id)
            .cloned()
            .ok_or_else(|| "maker-taker leader leg is missing".to_string())?;
        let hedge_index = self
            .legs
            .iter()
            .position(|leg| leg.leg_id == spec.hedge_leg_id)
            .ok_or_else(|| "maker-taker hedge leg is missing".to_string())?;
        let hedge = self.legs[hedge_index].clone();
        let net_leader_filled =
            subtract_saturating(leader.filled_quantity, unwind_filled_quantity)?;
        let required = spec.required_hedge_quantity(net_leader_filled)?;
        let unhedged_filled = subtract_saturating(required, hedge.filled_quantity)?;
        let after_hedge_commitment =
            subtract_saturating(unhedged_filled, hedge.committed_quantity)?;
        let unwind_commitment_equivalent =
            spec.required_hedge_quantity(unwind_committed_quantity)?;
        let unhedged_after_commitment =
            subtract_saturating(after_hedge_commitment, unwind_commitment_equivalent)?;
        let previous = self.exposure.as_ref();
        let unhedged_since = if unhedged_filled.is_zero() {
            None
        } else if previous.is_some_and(|ledger| !ledger.unhedged_filled_quantity.is_zero()) {
            previous
                .and_then(|ledger| ledger.unhedged_since)
                .or(exposure_observed_at)
        } else {
            exposure_observed_at
        };
        self.exposure = Some(NormalizedExposureLedger {
            leader_filled_quantity: leader.filled_quantity,
            unwind_filled_quantity,
            unwind_committed_quantity,
            net_leader_filled_quantity: net_leader_filled,
            required_hedge_quantity: required,
            hedge_filled_quantity: hedge.filled_quantity,
            hedge_committed_quantity: hedge.committed_quantity,
            unhedged_filled_quantity: unhedged_filled,
            unhedged_after_commitment,
            unhedged_since,
        });
        if !matches!(
            self.legs[hedge_index].lifecycle,
            AlgorithmLegLifecycle::ReconciliationRequired | AlgorithmLegLifecycle::Failed
        ) {
            self.legs[hedge_index].lifecycle = if required.is_zero() {
                AlgorithmLegLifecycle::Dormant
            } else if leader.filled_quantity >= leader.target_quantity
                && unhedged_filled <= spec.max_unhedged_quantity
                && (unhedged_filled.is_zero() || spec.max_unhedged_duration.is_none())
                && hedge.committed_quantity.is_zero()
            {
                AlgorithmLegLifecycle::Completed
            } else if !hedge.committed_quantity.is_zero() {
                AlgorithmLegLifecycle::Active
            } else {
                AlgorithmLegLifecycle::Ready
            };
        }
        self.validate()
    }
}

pub fn decide_maker_taker_hedge(
    run: &AlgorithmRun,
    mut input: AlgorithmInput,
) -> Result<AlgorithmDecision, String> {
    let ExecutionAlgorithmSpec::MakerTakerHedge(spec) = &run.spec else {
        return Err("maker-taker decision received a different algorithm spec".into());
    };
    run.validate()?;
    if run
        .last_decision_at
        .is_some_and(|current| input.business_time < current)
    {
        return Err("algorithm business time cannot move backwards".into());
    }
    if matches!(
        run.status,
        AlgorithmRunStatus::Completed
            | AlgorithmRunStatus::Unwound
            | AlgorithmRunStatus::Failed
            | AlgorithmRunStatus::ReconciliationRequired
    ) {
        return Ok(no_actions(run, input.business_time, run.status));
    }
    if run.pending_actions().next().is_some() {
        return Ok(no_actions(
            run,
            input.business_time,
            AlgorithmRunStatus::Waiting,
        ));
    }
    if run
        .legs
        .iter()
        .any(|leg| leg.lifecycle == AlgorithmLegLifecycle::ReconciliationRequired)
    {
        return Ok(AlgorithmDecision {
            expected_sequence: run.decision_sequence,
            decided_at: input.business_time,
            next_status: AlgorithmRunStatus::ReconciliationRequired,
            next_wake_at: None,
            actions: vec![AlgorithmActionKind::RequireReconciliation {
                reason: "maker or hedge leg requires reconciliation".into(),
            }],
        });
    }
    let exposure = run
        .exposure
        .as_ref()
        .ok_or_else(|| "maker-taker exposure ledger is missing".to_string())?;
    input
        .ready_children
        .sort_by(|left, right| left.order_id.cmp(&right.order_id));

    let hedge = run
        .legs
        .iter()
        .find(|leg| leg.leg_id == spec.hedge_leg_id)
        .ok_or_else(|| "maker-taker hedge leg is missing".to_string())?;
    let hedge_due = spec.hedge_due(exposure, input.business_time)?;
    if hedge.lifecycle == AlgorithmLegLifecycle::Failed
        && hedge_due
        && !exposure.unhedged_after_commitment.is_zero()
    {
        if let Some(candidate) = input.ready_children.iter().find(|candidate| {
            candidate.leg_id == spec.hedge_leg_id
                && candidate.execution_style == AlgorithmExecutionStyle::TakerImmediate
        }) {
            if candidate.quantity < exposure.unhedged_after_commitment {
                return Err("ready fallback hedge cannot cover current exposure".into());
            }
            return Ok(submit_decision(
                run,
                input.business_time,
                candidate.order_id.clone(),
                candidate.leg_id.clone(),
                exposure.unhedged_after_commitment,
                AlgorithmExecutionStyle::TakerImmediate,
                candidate.execution_route_id.clone(),
            ));
        }
        let unwind_quantity =
            spec.leader_quantity_for_hedge_exposure(exposure.unhedged_after_commitment)?;
        let candidate = input
            .ready_children
            .iter()
            .find(|candidate| {
                candidate.leg_id == spec.leader_leg_id
                    && candidate.execution_style == AlgorithmExecutionStyle::UnwindImmediate
            })
            .ok_or_else(|| {
                "failed taker hedge requires a ready fallback hedge or unwind child".to_string()
            })?;
        if candidate.quantity < unwind_quantity {
            return Err("ready unwind child cannot close current exposure".into());
        }
        return Ok(submit_decision(
            run,
            input.business_time,
            candidate.order_id.clone(),
            candidate.leg_id.clone(),
            unwind_quantity,
            AlgorithmExecutionStyle::UnwindImmediate,
            candidate.execution_route_id.clone(),
        ));
    }

    if hedge_due && !exposure.unhedged_after_commitment.is_zero() {
        let candidate = input
            .ready_children
            .iter()
            .find(|candidate| {
                candidate.leg_id == spec.hedge_leg_id
                    && candidate.execution_style == AlgorithmExecutionStyle::TakerImmediate
            })
            .ok_or_else(|| "unhedged exposure requires a ready taker hedge child".to_string())?;
        if candidate.quantity < exposure.unhedged_after_commitment {
            return Err("ready taker hedge child cannot cover current exposure".into());
        }
        return Ok(submit_decision(
            run,
            input.business_time,
            candidate.order_id.clone(),
            candidate.leg_id.clone(),
            exposure.unhedged_after_commitment,
            AlgorithmExecutionStyle::TakerImmediate,
            candidate.execution_route_id.clone(),
        ));
    }

    let leader = run
        .legs
        .iter()
        .find(|leg| leg.leg_id == spec.leader_leg_id)
        .ok_or_else(|| "maker-taker leader leg is missing".to_string())?;
    if leader.filled_quantity >= leader.target_quantity
        && !hedge_due
        && (exposure.unhedged_filled_quantity.is_zero() || spec.max_unhedged_duration.is_none())
        && exposure.hedge_committed_quantity.is_zero()
    {
        return Ok(AlgorithmDecision {
            expected_sequence: run.decision_sequence,
            decided_at: input.business_time,
            next_status: AlgorithmRunStatus::Completed,
            next_wake_at: None,
            actions: vec![AlgorithmActionKind::Complete],
        });
    }

    if !exposure.unhedged_filled_quantity.is_zero() && !hedge_due {
        return Ok(AlgorithmDecision {
            expected_sequence: run.decision_sequence,
            decided_at: input.business_time,
            next_status: AlgorithmRunStatus::Waiting,
            next_wake_at: spec.exposure_deadline(exposure)?,
            actions: Vec::new(),
        });
    }

    let remaining = leader.remaining_uncommitted()?;
    if !remaining.is_zero() {
        let candidate = input.ready_children.iter().find(|candidate| {
            candidate.leg_id == spec.leader_leg_id
                && candidate.execution_style == AlgorithmExecutionStyle::MakerPostOnly
        });
        if let Some(candidate) = candidate {
            if candidate.quantity.is_zero() || candidate.quantity > remaining {
                return Err("ready maker child exceeds the leader's uncommitted quantity".into());
            }
            return Ok(submit_decision(
                run,
                input.business_time,
                candidate.order_id.clone(),
                candidate.leg_id.clone(),
                candidate.quantity,
                AlgorithmExecutionStyle::MakerPostOnly,
                candidate.execution_route_id.clone(),
            ));
        }
    }

    Ok(no_actions(
        run,
        input.business_time,
        AlgorithmRunStatus::Waiting,
    ))
}

fn subtract_saturating(left: Quantity, right: Quantity) -> Result<Quantity, String> {
    if right >= left {
        Ok(Quantity::ZERO)
    } else {
        left.checked_sub(right).map_err(|error| error.to_string())
    }
}

fn no_actions(
    run: &AlgorithmRun,
    decided_at: kairos_primitives::time::UnixNanos,
    next_status: AlgorithmRunStatus,
) -> AlgorithmDecision {
    AlgorithmDecision {
        expected_sequence: run.decision_sequence,
        decided_at,
        next_status,
        next_wake_at: None,
        actions: Vec::new(),
    }
}

fn submit_decision(
    run: &AlgorithmRun,
    decided_at: kairos_primitives::time::UnixNanos,
    order_id: kairos_primitives::execution::OrderId,
    leg_id: kairos_primitives::execution::LegId,
    quantity: Quantity,
    execution_style: AlgorithmExecutionStyle,
    execution_route_id: Option<kairos_primitives::execution::ExecutionRouteId>,
) -> AlgorithmDecision {
    AlgorithmDecision {
        expected_sequence: run.decision_sequence,
        decided_at,
        next_status: AlgorithmRunStatus::Running,
        next_wake_at: None,
        actions: vec![AlgorithmActionKind::SubmitChild {
            order_id,
            leg_id,
            quantity,
            execution_style,
            execution_route_id,
        }],
    }
}
