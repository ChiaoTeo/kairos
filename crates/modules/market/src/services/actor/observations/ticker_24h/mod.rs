use super::super::MarketActor;
use crate::domain::observation::{MarketObservation, Ticker24h};

impl MarketActor {
    pub(crate) fn apply_ticker_24h(&mut self, value: Ticker24h) -> Result<u64, String> {
        self.apply_observation(MarketObservation::Ticker24h(value))
    }
}
