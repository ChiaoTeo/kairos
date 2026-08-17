use super::super::MarketActor;
use crate::domain::observation::{MarketObservation, Trade};

impl MarketActor {
    pub(crate) fn apply_trade(&mut self, value: Trade) -> Result<u64, String> {
        self.apply_observation(MarketObservation::Trade(value))
    }
}
