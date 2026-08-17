use crate::{MarketApplication, MarketError, QuoteBar};

impl MarketApplication {
    pub fn ingest_quote_bar(&mut self, value: QuoteBar) -> Result<u64, MarketError> {
        self.actor
            .apply_quote_bar(value)
            .map_err(MarketError::Invalid)
    }
}
