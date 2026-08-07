use crate::application::protocol::RiskStateStore;
use crate::application::RiskSnapshot;

#[derive(Default)]
pub struct MemoryRiskStore {
    pub snapshot: Option<RiskSnapshot>,
}

impl RiskStateStore for MemoryRiskStore {
    fn load(&mut self) -> Result<Option<RiskSnapshot>, String> {
        Ok(self.snapshot.clone())
    }
    fn save(&mut self, snapshot: &RiskSnapshot) -> Result<(), String> {
        self.snapshot = Some(snapshot.clone());
        Ok(())
    }
}
