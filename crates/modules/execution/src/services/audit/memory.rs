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
}

impl From<Vec<ExecutionEvent>> for MemoryExecutionAudit {
    fn from(events: Vec<ExecutionEvent>) -> Self {
        Self::new(events)
    }
}
