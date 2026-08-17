use crate::{MarketApplication, MarketError, TradeBar};

impl MarketApplication {
    pub fn ingest_trade_bar(&mut self, value: TradeBar) -> Result<u64, MarketError> {
        self.actor
            .apply_trade_bar(value)
            .map_err(MarketError::Invalid)
    }
}
