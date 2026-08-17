use crate::{MarketApplication, MarketError, Trade};

impl MarketApplication {
    pub fn ingest_trade(&mut self, value: Trade) -> Result<u64, MarketError> {
        self.actor.apply_trade(value).map_err(MarketError::Invalid)
    }
}
