//! Minimal capabilities consumed by the Risk application.

use super::RiskSnapshot;

pub trait RiskStateStore: Send {
    fn load(&mut self) -> Result<Option<RiskSnapshot>, String>;
    fn save(&mut self, snapshot: &RiskSnapshot) -> Result<(), String>;
}
