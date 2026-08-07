use crate::application::ExecutionSnapshot;

pub trait ExecutionStateStore: Send {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String>;
    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String>;
}
