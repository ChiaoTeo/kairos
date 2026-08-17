use crate::{MarketApplication, MarketError, OptionGreeks};

impl MarketApplication {
    pub fn ingest_option_greeks(&mut self, value: OptionGreeks) -> Result<u64, MarketError> {
        self.actor
            .apply_option_greeks(value)
            .map_err(MarketError::Invalid)
    }
}
