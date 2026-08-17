use super::super::MarketActor;
use crate::domain::observation::{MarketObservation, TradeBar};

impl MarketActor {
    pub(crate) fn apply_trade_bar(&mut self, value: TradeBar) -> Result<u64, String> {
        self.apply_observation(MarketObservation::TradeBar(value))
    }
}
