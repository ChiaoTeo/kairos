use super::super::MarketActor;
use crate::domain::observation::{MarkPrice, MarketObservation};

impl MarketActor {
    pub(crate) fn apply_mark_price(&mut self, value: MarkPrice) -> Result<u64, String> {
        self.apply_observation(MarketObservation::MarkPrice(value))
    }
}
