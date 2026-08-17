use crate::{Bar, MarketApplication, MarketError};

impl MarketApplication {
    pub fn ingest_bar(&mut self, value: Bar) -> Result<u64, MarketError> {
        self.actor.apply_bar(value).map_err(MarketError::Invalid)
    }
}
