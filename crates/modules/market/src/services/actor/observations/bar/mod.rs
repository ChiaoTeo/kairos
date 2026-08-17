use super::super::MarketActor;
use crate::domain::observation::{Bar, MarketObservation};

impl MarketActor {
    pub(crate) fn apply_bar(&mut self, value: Bar) -> Result<u64, String> {
        self.apply_observation(MarketObservation::Bar(value))
    }
}
