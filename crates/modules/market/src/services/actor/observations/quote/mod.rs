use super::super::MarketActor;
use crate::domain::observation::{MarketObservation, Quote};

impl MarketActor {
    pub(crate) fn apply_quote(&mut self, value: Quote) -> Result<u64, String> {
        self.apply_observation(MarketObservation::Quote(value))
    }
}
