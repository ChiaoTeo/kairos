use super::super::MarketActor;
use crate::domain::observation::{MarketObservation, OptionGreeks};

impl MarketActor {
    pub(crate) fn apply_option_greeks(&mut self, value: OptionGreeks) -> Result<u64, String> {
        self.apply_observation(MarketObservation::OptionGreeks(value))
    }
}
