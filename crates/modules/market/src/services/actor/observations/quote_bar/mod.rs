use super::super::MarketActor;
use crate::domain::observation::{MarketObservation, QuoteBar};

impl MarketActor {
    pub(crate) fn apply_quote_bar(&mut self, value: QuoteBar) -> Result<u64, String> {
        self.apply_observation(MarketObservation::QuoteBar(value))
    }
}
