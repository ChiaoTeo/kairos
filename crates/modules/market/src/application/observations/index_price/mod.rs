use crate::{IndexPrice, MarketApplication, MarketError};

impl MarketApplication {
    pub fn ingest_index_price(&mut self, value: IndexPrice) -> Result<u64, MarketError> {
        self.actor
            .apply_index_price(value)
            .map_err(MarketError::Invalid)
    }
}
