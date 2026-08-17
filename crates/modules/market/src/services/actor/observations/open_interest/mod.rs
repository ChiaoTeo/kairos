use super::super::MarketActor;
use crate::domain::observation::{MarketObservation, OpenInterest};

impl MarketActor {
    pub(crate) fn apply_open_interest(&mut self, value: OpenInterest) -> Result<u64, String> {
        self.apply_observation(MarketObservation::OpenInterest(value))
    }
}
