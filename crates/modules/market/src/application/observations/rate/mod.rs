use crate::{MarketApplication, MarketError, Rate};

impl MarketApplication {
    pub fn ingest_rate(&mut self, value: Rate) -> Result<u64, MarketError> {
        self.actor.apply_rate(value).map_err(MarketError::Invalid)
    }
}
