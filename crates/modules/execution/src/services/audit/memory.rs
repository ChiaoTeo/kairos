use super::{ExecutionAuditEvent, ExecutionAuditQuery};
use crate::application::{ExecutionEvent, IntentEvent};

pub struct MemoryExecutionAudit {
    order_events: Vec<ExecutionEvent>,
    intent_events: Vec<IntentEvent>,
}

impl MemoryExecutionAudit {
    pub fn new(order_events: Vec<ExecutionEvent>) -> Self {
        Self {
            order_events,
            intent_events: Vec::new(),
        }
    }

    pub(super) fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intents: &[IntentEvent],
    ) -> Result<(), String> {
        self.order_events.extend_from_slice(events);
        self.intent_events.extend_from_slice(intents);
        Ok(())
    }

    pub(super) fn query(
        &mut self,
        query: &ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, String> {
        Ok(self
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
            })
            .filter(|event| {
                query
                    .order_id
                    .as_deref()
                    .is_none_or(|value| event.order_id.as_str() == value)
                    && query.status.as_deref().is_none_or(|value| {
                        format!("{:?}", event.status).eq_ignore_ascii_case(value)
                    })
            })
            .take(query.limit.unwrap_or(u32::MAX) as usize)
            .collect())
    }
}

impl From<Vec<ExecutionEvent>> for MemoryExecutionAudit {
    fn from(events: Vec<ExecutionEvent>) -> Self {
        Self::new(events)
    }
}
