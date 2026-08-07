//! Minimal capabilities consumed by the Risk application.

use super::{RiskEvent, RiskSnapshot};

pub trait RiskEventSink: Send {
    fn publish(&mut self, event: &RiskEvent) -> Result<(), String>;
}

pub trait RiskPublisher: Send {
    fn publish(&mut self, snapshot: &RiskSnapshot) -> Result<(), String>;
}

pub trait RiskStateStore: Send {
    fn load(&mut self) -> Result<Option<RiskSnapshot>, String>;
    fn save(&mut self, snapshot: &RiskSnapshot) -> Result<(), String>;
}
