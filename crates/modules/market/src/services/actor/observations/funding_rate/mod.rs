use super::super::MarketActor;
use crate::domain::observation::{FundingRate, MarketObservation};

impl MarketActor {
    pub(crate) fn apply_funding_rate(&mut self, value: FundingRate) -> Result<u64, String> {
        self.apply_observation(MarketObservation::FundingRate(value))
    }
}
