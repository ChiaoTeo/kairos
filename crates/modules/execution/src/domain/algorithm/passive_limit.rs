use super::{
    AlgorithmActionKind, AlgorithmDecision, AlgorithmError, AlgorithmExecutionStyle,
    AlgorithmInput, AlgorithmInvariant, AlgorithmLegLifecycle, AlgorithmRun, AlgorithmRunStatus,
    ExecutionAlgorithmSpec,
};

/// Authorize passive children from explicit, already validated quote input.
/// Quote selection and freshness evidence stay outside this pure kernel; the
/// resulting candidate identity and quantity are persisted in the decision.
pub fn decide_passive_limit(
    run: &AlgorithmRun,
    mut input: AlgorithmInput,
) -> Result<AlgorithmDecision, AlgorithmError> {
    let ExecutionAlgorithmSpec::PassiveLimit(spec) = &run.spec else {
        return Err(AlgorithmError::SpecMismatch {
            expected: "passive-limit",
        });
    };
    spec.validate()?;
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
                reason: "a passive-limit leg requires reconciliation".into(),
            }],
        });
    }
    if run.legs.iter().all(|leg| {
        leg.lifecycle == AlgorithmLegLifecycle::Completed
            || leg.filled_quantity >= leg.target_quantity
    }) {
        return Ok(AlgorithmDecision {
            expected_sequence: run.decision_sequence,
            decided_at: input.business_time,
            next_status: AlgorithmRunStatus::Completed,
            next_wake_at: None,
            actions: vec![AlgorithmActionKind::Complete],
        });
    }
    input
        .ready_children
        .sort_by(|left, right| left.order_id.cmp(&right.order_id));
    let candidates = input
        .ready_children
        .into_iter()
        .filter(|candidate| candidate.execution_style == AlgorithmExecutionStyle::PassiveLimit)
        .collect::<Vec<_>>();
    let mut order_ids = std::collections::BTreeSet::new();
    let mut leg_ids = std::collections::BTreeSet::new();
    let mut actions = Vec::with_capacity(candidates.len());
    for candidate in candidates {
        if candidate.quantity.is_zero() {
            return Err(AlgorithmError::invariant(
                AlgorithmInvariant::ReadyChildQuantityNotPositive,
            ));
        }
        if !order_ids.insert(candidate.order_id.clone())
            || !leg_ids.insert(candidate.leg_id.clone())
        {
            return Err(AlgorithmError::invariant(
                AlgorithmInvariant::DuplicateReadyChild,
            ));
        }
        let leg = run
            .legs
            .iter()
            .find(|leg| leg.leg_id == candidate.leg_id)
            .ok_or_else(|| AlgorithmError::MissingLeg {
                leg_id: candidate.leg_id.to_string(),
            })?;
        if candidate.quantity > leg.target_quantity {
            return Err(AlgorithmError::invariant(
                AlgorithmInvariant::ChildExceedsLegTarget,
            ));
        }
        actions.push(AlgorithmActionKind::SubmitChild {
            order_id: candidate.order_id,
            leg_id: candidate.leg_id,
            quantity: candidate.quantity,
            execution_style: AlgorithmExecutionStyle::PassiveLimit,
            execution_route_id: candidate.execution_route_id,
        });
    }
    Ok(AlgorithmDecision {
        expected_sequence: run.decision_sequence,
        decided_at: input.business_time,
        next_status: if actions.is_empty() {
            AlgorithmRunStatus::Waiting
        } else {
            AlgorithmRunStatus::Running
        },
        next_wake_at: None,
        actions,
    })
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
