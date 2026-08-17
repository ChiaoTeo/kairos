use crate::{MarketApplication, MarketError, OpenInterest};

impl MarketApplication {
    pub fn ingest_open_interest(&mut self, value: OpenInterest) -> Result<u64, MarketError> {
        self.actor
            .apply_open_interest(value)
            .map_err(MarketError::Invalid)
    }
}
