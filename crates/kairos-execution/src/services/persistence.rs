use crate::application::protocol::ExecutionStateStore;
use crate::application::ExecutionSnapshot;

#[derive(Default)]
pub struct MemoryExecutionStore {
    pub snapshot: Option<ExecutionSnapshot>,
}

impl ExecutionStateStore for MemoryExecutionStore {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String> {
        Ok(self.snapshot.clone())
    }

    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        self.snapshot = Some(snapshot.clone());
        Ok(())
    }
}
