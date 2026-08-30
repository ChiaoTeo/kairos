use kairos_primitives::decimal::Money;
use kairos_primitives::time::UnixNanos;

use crate::domain::{
    RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult, RiskReservationEvidence,
    RiskReservationSagaStatus, SubmitOrder,
};

#[derive(Clone, Debug)]
pub enum SimulatedRiskReconciliation {
    Echo,
    Missing,
    Observed {
        status: RiskReservationSagaStatus,
        event_sequence: u64,
    },
}

#[derive(Clone, Debug)]
pub struct SimulatedRiskBehavior {
    pub authorization_failure: Option<RiskCommandFailure>,
    pub release_failure: Option<RiskCommandFailure>,
    pub reconciliation: SimulatedRiskReconciliation,
}

impl Default for SimulatedRiskBehavior {
    fn default() -> Self {
        Self {
            authorization_failure: None,
            release_failure: None,
            reconciliation: SimulatedRiskReconciliation::Echo,
        }
    }
}

pub(super) struct SimulatedRiskReservations {
    behavior: SimulatedRiskBehavior,
}

impl SimulatedRiskReservations {
    pub(super) fn new(behavior: SimulatedRiskBehavior) -> Self {
        Self { behavior }
    }

    pub(super) fn authorize(
        &mut self,
        request: &SubmitOrder,
        _context: &RiskAuthorizationContext,
    ) -> RiskCommandResult<RiskReservationEvidence> {
        if let Some(error) = self.behavior.authorization_failure.clone() {
            return Err(error);
        }
        let amount = Money::new(request.quantity.mantissa(), request.quantity.scale())
            .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?;
        let at = request.submitted_at_unix_nanos.unwrap_or_default();
        Ok(RiskReservationEvidence {
            order_id: request.order_id.clone(),
            reservation_id: kairos_primitives::risk::ReservationId::new(format!(
                "execution:{}",
                request.order_id
            ))
            .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?,
            idempotency_key: kairos_primitives::runtime::IdempotencyKey::new(format!(
                "execution:{}",
                request.order_id
            ))
            .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?,
            account_id: request.account_id.clone(),
            amount,
            status: RiskReservationSagaStatus::Active,
            risk_generation: 1.into(),
            risk_event_sequence: 1.into(),
            policy_version: 1.into(),
            expires_at_unix_nanos: UnixNanos::new(at.get().saturating_add(60_000_000_000)),
            updated_at_unix_nanos: at,
            funding_requirement: None,
        })
    }

    pub(super) fn reconcile(
        &mut self,
        evidence: &RiskReservationEvidence,
    ) -> Result<Option<RiskReservationEvidence>, String> {
        Ok(match self.behavior.reconciliation {
            SimulatedRiskReconciliation::Echo => Some(evidence.clone()),
            SimulatedRiskReconciliation::Missing => None,
            SimulatedRiskReconciliation::Observed {
                status,
                event_sequence,
            } => {
                let mut observed = evidence.clone();
                observed.status = status;
                observed.risk_generation = 7.into();
                observed.risk_event_sequence = event_sequence.into();
                Some(observed)
            },
        })
    }

    pub(super) fn resize(
        &mut self,
        _evidence: &RiskReservationEvidence,
        _amount: Money,
        _at: UnixNanos,
    ) -> RiskCommandResult<()> {
        Ok(())
    }

    pub(super) fn release(
        &mut self,
        _evidence: &RiskReservationEvidence,
        _at: UnixNanos,
    ) -> RiskCommandResult<()> {
        match self.behavior.release_failure.clone() {
            Some(error) => Err(error),
            None => Ok(()),
        }
    }

    pub(super) fn consume(
        &mut self,
        _evidence: &RiskReservationEvidence,
        _at: UnixNanos,
    ) -> RiskCommandResult<()> {
        Ok(())
    }
}
