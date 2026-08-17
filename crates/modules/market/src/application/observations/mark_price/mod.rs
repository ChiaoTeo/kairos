use crate::{MarkPrice, MarketApplication, MarketError};

impl MarketApplication {
    pub fn ingest_mark_price(&mut self, value: MarkPrice) -> Result<u64, MarketError> {
        self.actor
            .apply_mark_price(value)
            .map_err(MarketError::Invalid)
    }
}
