use super::super::MarketActor;
use crate::domain::observation::{IndexPrice, MarketObservation};

impl MarketActor {
    pub(crate) fn apply_index_price(&mut self, value: IndexPrice) -> Result<u64, String> {
        self.apply_observation(MarketObservation::IndexPrice(value))
    }
}
