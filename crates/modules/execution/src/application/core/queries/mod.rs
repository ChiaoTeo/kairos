//! Read-only order, fill, commitment, reservation, and audit use cases.

use kairos_primitives::execution::FillId;

use super::*;

impl ExecutionApplication {
    pub(crate) fn simulated_settlement_fact(
        &self,
        fill_id: &FillId,
    ) -> Result<(ExecutionFill, ExecutionOrder, OrderCommitment), OrderError> {
        let fill = self
            .actor
            .fills()
            .iter()
            .find(|fill| &fill.fill_id == fill_id)
            .ok_or_else(|| OrderError::MissingFill {
                fill_id: fill_id.to_string(),
            })?
            .clone();
        let order = self
            .actor
            .order_map()
            .get(&fill.order_id)
            .cloned()
            .ok_or_else(|| OrderError::MissingOrderForFill {
                fill_id: fill.fill_id.to_string(),
                order_id: fill.order_id.clone(),
            })?;
        let commitment = self
            .actor
            .commitment(fill.order_id.as_str())
            .cloned()
            .ok_or_else(|| OrderError::MissingCommitmentForFill {
                fill_id: fill.fill_id.to_string(),
                order_id: fill.order_id.clone(),
            })?;
        Ok((fill, order, commitment))
    }

    pub fn orders(&self, account_id: Option<&str>) -> Vec<ExecutionOrder> {
        self.actor
            .order_map()
            .values()
            .filter(|order| account_id.is_none_or(|id| order.account_id == id))
            .cloned()
            .collect()
    }

    pub fn events(&self, order_id: Option<&str>) -> Vec<ExecutionEvent> {
        self.actor
            .events()
            .iter()
            .filter(|event| order_id.is_none_or(|id| event.order_id.as_str() == id))
            .cloned()
            .collect()
    }

    pub fn trace(&self, order_id: &str) -> Vec<ExecutionEvent> {
        self.events(Some(order_id))
    }

    pub fn audit_events(
        &mut self,
        query: ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, ExecutionError> {
        let mut events = self
            .actor
            .events()
            .iter()
            .enumerate()
            .map(|(index, event)| ExecutionAuditEvent {
                sequence: (index as u64 + 1).into(),
                order_id: event.order_id.clone(),
                status: event.status,
                remote_order_id: event.remote_order_id.as_ref().cloned(),
                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                reason: event.reason.clone(),
                attempt: event.attempt.clone(),
            })
            .filter(|event| audit_matches(event, &query))
            .collect::<Vec<_>>();
        if let Some(limit) = query.limit {
            events.truncate(limit as usize);
        }
        Ok(events)
    }

    pub fn fills(&self, order_id: Option<&str>) -> Vec<ExecutionFill> {
        self.actor
            .fills()
            .iter()
            .filter(|fill| order_id.is_none_or(|id| fill.order_id == id))
            .cloned()
            .collect()
    }

    pub fn commitments(&self) -> Vec<OrderCommitment> {
        self.actor.commitments().cloned().collect()
    }

    pub fn risk_reservations(&self) -> Vec<RiskReservationEvidence> {
        self.actor.risk_reservations().cloned().collect()
    }
}
