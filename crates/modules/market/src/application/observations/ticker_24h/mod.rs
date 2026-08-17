use crate::{MarketApplication, MarketError, Ticker24h};

impl MarketApplication {
    pub fn ingest_ticker_24h(&mut self, value: Ticker24h) -> Result<u64, MarketError> {
        self.actor
            .apply_ticker_24h(value)
            .map_err(MarketError::Invalid)
    }
}
