use kairos_primitives::decimal::Quantity;
use kairos_primitives::execution::{ExecutionRouteId, LegId, OrderId};
use kairos_primitives::time::UnixNanos;

use super::{
    AlgorithmAction, AlgorithmActionStatus, AlgorithmDecisionSequence, AlgorithmExecutionStyle,
    AlgorithmRun, AlgorithmRunStatus,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AlgorithmChildCandidate {
    pub order_id: OrderId,
    pub leg_id: LegId,
    pub quantity: Quantity,
    pub execution_style: AlgorithmExecutionStyle,
    pub execution_route_id: Option<ExecutionRouteId>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AlgorithmInput {
    pub business_time: UnixNanos,
    pub ready_children: Vec<AlgorithmChildCandidate>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AlgorithmDecision {
    pub expected_sequence: AlgorithmDecisionSequence,
    pub decided_at: UnixNanos,
    pub next_status: AlgorithmRunStatus,
    pub next_wake_at: Option<UnixNanos>,
    pub actions: Vec<super::AlgorithmActionKind>,
}

impl AlgorithmRun {
    pub fn apply_decision(&mut self, decision: AlgorithmDecision) -> Result<(), String> {
        if decision.expected_sequence != self.decision_sequence {
            return Err(format!(
                "stale algorithm decision: expected {}, current {}",
                decision.expected_sequence, self.decision_sequence
            ));
        }
        if self
            .last_decision_at
            .is_some_and(|current| decision.decided_at < current)
        {
            return Err("algorithm business time cannot move backwards".into());
        }
        let next_sequence = self
            .decision_sequence
            .checked_next()
            .ok_or_else(|| "algorithm decision sequence overflow".to_string())?;
        for (index, kind) in decision.actions.into_iter().enumerate() {
            self.actions.push(AlgorithmAction {
                action_id: format!(
                    "{}:decision:{next_sequence}:action:{}",
                    self.algorithm_run_id,
                    index + 1
                ),
                decision_sequence: next_sequence,
                status: AlgorithmActionStatus::Pending,
                kind,
            });
        }
        self.status = decision.next_status;
        self.next_wake_at = decision.next_wake_at;
        self.last_decision_at = Some(decision.decided_at);
        self.decision_sequence = next_sequence;
        self.validate()
    }

    pub fn set_action_status(
        &mut self,
        action_id: &str,
        status: AlgorithmActionStatus,
    ) -> Result<(), String> {
        let action = self
            .actions
            .iter_mut()
            .find(|action| action.action_id == action_id)
            .ok_or_else(|| "unknown algorithm action".to_string())?;
        if action.status != AlgorithmActionStatus::Pending {
            return Err("algorithm action is already resolved".into());
        }
        action.status = status;
        Ok(())
    }
}
