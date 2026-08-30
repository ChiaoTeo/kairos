use crate::domain::{ExecutionEvent, ExecutionSnapshot, IntentEvent};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionOutboxEvent {
    Order(ExecutionEvent),
    Intent(IntentEvent),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionOutboxEntry {
    pub id: u64,
    /// Wall-clock creation time retained only for backlog-age telemetry.
    pub created_at_unix_nanos: u64,
    pub event: ExecutionOutboxEvent,
}

/// Internal persistence capability for the Execution actor.
///
/// Persistence is selected by composition and is not an application protocol.
pub trait ExecutionStateStore: Send {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String>;
    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String>;

    fn commit_event(
        &mut self,
        event: &ExecutionEvent,
        snapshot: &ExecutionSnapshot,
    ) -> Result<(), String> {
        self.append_event(event)?;
        self.save(snapshot)
    }

    fn commit_intent_event(
        &mut self,
        event: &IntentEvent,
        snapshot: &ExecutionSnapshot,
    ) -> Result<(), String> {
        self.append_intent_event(event)?;
        self.save(snapshot)
    }

    fn append_event(&mut self, _event: &ExecutionEvent) -> Result<(), String> {
        Ok(())
    }

    fn append_intent_event(&mut self, _event: &IntentEvent) -> Result<(), String> {
        Ok(())
    }

    fn pending_outbox(&mut self, _limit: u32) -> Result<Vec<ExecutionOutboxEntry>, String> {
        Ok(Vec::new())
    }

    fn acknowledge_outbox(&mut self, _ids: &[u64]) -> Result<(), String> {
        Ok(())
    }

    /// Returns the timestamp of the most recently durable checkpoint, if this
    /// store persists checkpoints. This is operational metadata, not business
    /// state, and is used only to expose checkpoint freshness.
    fn latest_checkpoint_unix_nanos(&mut self) -> Result<Option<u64>, String> {
        Ok(None)
    }
}
