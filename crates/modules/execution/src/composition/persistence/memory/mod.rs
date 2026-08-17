//! In-memory persistence and audit implementations for tests and local modes.

use crate::application::ExecutionSnapshot;
use crate::services::persistence::ExecutionStateStore;

pub struct MemoryStateStore(pub Option<ExecutionSnapshot>);

impl ExecutionStateStore for MemoryStateStore {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String> {
        Ok(self.0.clone())
    }

    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        self.0 = Some(snapshot.clone());
        Ok(())
    }
}
