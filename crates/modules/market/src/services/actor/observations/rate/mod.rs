use super::super::MarketActor;
use crate::domain::observation::{MarketObservation, Rate};

impl MarketActor {
    pub(crate) fn apply_rate(&mut self, value: Rate) -> Result<u64, String> {
        self.apply_observation(MarketObservation::Rate(value))
    }
}
