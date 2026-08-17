use crate::{FundingRate, MarketApplication, MarketError};

impl MarketApplication {
    pub fn ingest_funding_rate(&mut self, value: FundingRate) -> Result<u64, MarketError> {
        self.actor
            .apply_funding_rate(value)
            .map_err(MarketError::Invalid)
    }
}
