use super::{ExecutionAuditEvent, ExecutionAuditQuery, IntentAdmissionAuditRecord};
use crate::application::{ExecutionEvent, IntentEvent};

pub struct MemoryExecutionAudit {
    order_events: Vec<ExecutionEvent>,
    intent_events: Vec<IntentEvent>,
    admissions: Vec<IntentAdmissionAuditRecord>,
}

impl MemoryExecutionAudit {
    pub fn new(order_events: Vec<ExecutionEvent>) -> Self {
        Self {
            order_events,
            intent_events: Vec::new(),
            admissions: Vec::new(),
        }
    }

    pub(super) fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intents: &[IntentEvent],
        admissions: &[IntentAdmissionAuditRecord],
    ) -> Result<(), String> {
        self.order_events.extend_from_slice(events);
        self.intent_events.extend_from_slice(intents);
        for record in admissions {
            if let Some(existing) = self
                .admissions
                .iter_mut()
                .find(|existing| existing.evidence.decision_id == record.evidence.decision_id)
            {
                if existing.command_id != record.command_id
                    || existing.idempotency_key != record.idempotency_key
                    || existing.intent_id != record.intent_id
                    || existing.evidence != record.evidence
                {
                    return Err(
                        "Decision admission evidence was reused with different facts".into(),
                    );
                }
                existing
                    .admission_result
                    .clone_from(&record.admission_result);
            } else {
                self.admissions.push(record.clone());
            }
        }
        Ok(())
    }

    pub(super) fn query(&self, query: &ExecutionAuditQuery) -> Vec<ExecutionAuditEvent> {
        let mut events = self
            .order_events
            .iter()
            .enumerate()
            .map(|(index, event)| ExecutionAuditEvent {
                sequence: (index as u64 + 1).into(),
                order_id: event.order_id.clone(),
                status: event.status,
                remote_order_id: event.remote_order_id.clone(),
                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                reason: event.reason.clone(),
                attempt: event.attempt.clone(),
            })
            .filter(|event| {
                query
                    .order_id
                    .as_ref()
                    .is_none_or(|value| value == &event.order_id)
                    && query
                        .remote_order_id
                        .as_ref()
                        .is_none_or(|value| event.remote_order_id.as_ref() == Some(value))
                    && query.status.as_ref().is_none_or(|value| {
                        format!("{:?}", event.status).eq_ignore_ascii_case(value)
                    })
                    && query
                        .since_unix_nanos
                        .is_none_or(|value| event.occurred_at_unix_nanos >= value)
                    && query
                        .until_unix_nanos
                        .is_none_or(|value| event.occurred_at_unix_nanos <= value)
            })
            .collect::<Vec<_>>();
        events.truncate(query.limit.unwrap_or(10_000) as usize);
        events
    }
}

impl From<Vec<ExecutionEvent>> for MemoryExecutionAudit {
    fn from(events: Vec<ExecutionEvent>) -> Self {
        Self::new(events)
    }
}
