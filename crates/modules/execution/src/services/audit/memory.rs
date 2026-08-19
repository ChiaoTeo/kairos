use super::IntentAdmissionAuditRecord;
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
}

impl From<Vec<ExecutionEvent>> for MemoryExecutionAudit {
    fn from(events: Vec<ExecutionEvent>) -> Self {
        Self::new(events)
    }
}
