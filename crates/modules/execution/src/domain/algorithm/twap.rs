use super::{
    AlgorithmActionKind, AlgorithmDecision, AlgorithmError, AlgorithmExecutionStyle,
    AlgorithmInput, AlgorithmInvariant, AlgorithmLegLifecycle, AlgorithmRun, AlgorithmRunStatus,
    ExecutionAlgorithmSpec,
};

pub fn decide_twap(
    run: &AlgorithmRun,
    mut input: AlgorithmInput,
) -> Result<AlgorithmDecision, AlgorithmError> {
    let ExecutionAlgorithmSpec::Twap(spec) = &run.spec else {
        return Err(AlgorithmError::SpecMismatch { expected: "TWAP" });
    };
    run.validate()?;
    if run
        .last_decision_at
        .is_some_and(|current| input.business_time < current)
    {
        return Err(AlgorithmError::BusinessTimeRegression);
    }
    if matches!(
        run.status,
        AlgorithmRunStatus::Completed
            | AlgorithmRunStatus::Unwound
            | AlgorithmRunStatus::Failed
            | AlgorithmRunStatus::ReconciliationRequired
    ) {
        return Ok(no_actions(run, input.business_time, run.status, None));
    }
    if run.pending_actions().next().is_some() {
        return Ok(no_actions(
            run,
            input.business_time,
            AlgorithmRunStatus::Waiting,
            run.next_wake_at,
        ));
    }
    let leg = run.legs.first().ok_or_else(|| AlgorithmError::MissingLeg {
        leg_id: spec.leg_id.to_string(),
    })?;
    if leg.lifecycle == AlgorithmLegLifecycle::ReconciliationRequired {
        return Ok(AlgorithmDecision {
            expected_sequence: run.decision_sequence,
            decided_at: input.business_time,
            next_status: AlgorithmRunStatus::ReconciliationRequired,
            next_wake_at: None,
            actions: vec![AlgorithmActionKind::RequireReconciliation {
                reason: "the TWAP leg requires reconciliation".into(),
            }],
        });
    }
    if leg.lifecycle == AlgorithmLegLifecycle::Completed
        || leg.filled_quantity >= leg.target_quantity
    {
        return Ok(AlgorithmDecision {
            expected_sequence: run.decision_sequence,
            decided_at: input.business_time,
            next_status: AlgorithmRunStatus::Completed,
            next_wake_at: None,
            actions: vec![AlgorithmActionKind::Complete],
        });
    }
    let slice_index = run
        .actions
        .iter()
        .filter(|action| {
            matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::TwapSlice,
                    ..
                }
            )
        })
        .count();
    if slice_index >= spec.slice_count as usize {
        return Ok(no_actions(
            run,
            input.business_time,
            AlgorithmRunStatus::Waiting,
            None,
        ));
    }
    let due_at = spec.due_at(slice_index as u32)?;
    if input.business_time < due_at {
        return Ok(no_actions(
            run,
            input.business_time,
            AlgorithmRunStatus::Waiting,
            Some(due_at),
        ));
    }
    input
        .ready_children
        .sort_by(|left, right| left.order_id.cmp(&right.order_id));
    let candidate = input
        .ready_children
        .into_iter()
        .find(|candidate| {
            candidate.leg_id == spec.leg_id
                && candidate.execution_style == AlgorithmExecutionStyle::TwapSlice
        })
        .ok_or(AlgorithmError::invariant(
            AlgorithmInvariant::MissingReadyTwapChild,
        ))?;
    if candidate.quantity.is_zero() || candidate.quantity > leg.remaining_uncommitted()? {
        return Err(AlgorithmError::invariant(
            AlgorithmInvariant::ChildExceedsLegTarget,
        ));
    }
    let next_slice = (slice_index as u32).saturating_add(1);
    Ok(AlgorithmDecision {
        expected_sequence: run.decision_sequence,
        decided_at: input.business_time,
        next_status: AlgorithmRunStatus::Running,
        next_wake_at: (next_slice < spec.slice_count)
            .then(|| spec.due_at(next_slice))
            .transpose()?,
        actions: vec![AlgorithmActionKind::SubmitChild {
            order_id: candidate.order_id,
            leg_id: candidate.leg_id,
            quantity: candidate.quantity,
            execution_style: AlgorithmExecutionStyle::TwapSlice,
            execution_route_id: candidate.execution_route_id,
        }],
    })
}

fn no_actions(
    run: &AlgorithmRun,
    decided_at: kairos_primitives::time::UnixNanos,
    next_status: AlgorithmRunStatus,
    next_wake_at: Option<kairos_primitives::time::UnixNanos>,
) -> AlgorithmDecision {
    AlgorithmDecision {
        expected_sequence: run.decision_sequence,
        decided_at,
        next_status,
        next_wake_at,
        actions: Vec::new(),
    }
}
