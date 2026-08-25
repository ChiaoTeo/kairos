use super::{
    AlgorithmActionKind, AlgorithmDecision, AlgorithmExecutionStyle, AlgorithmInput,
    AlgorithmLegLifecycle, AlgorithmRun, AlgorithmRunStatus, ExecutionAlgorithmSpec,
};

pub fn decide_immediate(
    run: &AlgorithmRun,
    mut input: AlgorithmInput,
) -> Result<AlgorithmDecision, String> {
    if run.spec != ExecutionAlgorithmSpec::Immediate {
        return Err("immediate decision received a different algorithm spec".into());
    }
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
        return Ok(AlgorithmDecision {
            expected_sequence: run.decision_sequence,
            decided_at: input.business_time,
            next_status: run.status,
            next_wake_at: None,
            actions: Vec::new(),
        });
    }
    if run.pending_actions().next().is_some() {
        return Ok(AlgorithmDecision {
            expected_sequence: run.decision_sequence,
            decided_at: input.business_time,
            next_status: AlgorithmRunStatus::Waiting,
            next_wake_at: None,
            actions: Vec::new(),
        });
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
                reason: "an immediate leg requires reconciliation".into(),
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
    let actions = if let Some(candidate) = input.ready_children.into_iter().next() {
        if candidate.quantity.is_zero() {
            return Err("ready child quantity must be positive".into());
        }
        let leg = run
            .legs
            .iter()
            .find(|leg| leg.leg_id == candidate.leg_id)
            .ok_or_else(|| "ready child references an unknown algorithm leg".to_string())?;
        if !matches!(
            leg.lifecycle,
            AlgorithmLegLifecycle::Ready | AlgorithmLegLifecycle::Active
        ) {
            return Err("ready child references an inactive algorithm leg".into());
        }
        if candidate.quantity > leg.remaining_uncommitted()? {
            return Err("ready child exceeds the leg's uncommitted quantity".into());
        }
        vec![AlgorithmActionKind::SubmitChild {
            order_id: candidate.order_id,
            leg_id: candidate.leg_id,
            quantity: candidate.quantity,
            execution_style: AlgorithmExecutionStyle::Immediate,
            execution_route_id: candidate.execution_route_id,
        }]
    } else {
        Vec::new()
    };
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
