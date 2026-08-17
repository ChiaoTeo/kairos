use crate::{MarketApplication, MarketError, Quote};

impl MarketApplication {
    pub fn ingest_quote(&mut self, value: Quote) -> Result<u64, MarketError> {
        self.actor.apply_quote(value).map_err(MarketError::Invalid)
    }
}
