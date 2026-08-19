use tracing::{debug, warn};

use super::super::{MarketApplication, MarketError};
use crate::domain::observation::MarketObservation;

impl MarketApplication {
    pub fn ingest(&mut self, observation: MarketObservation) -> Result<u64, MarketError> {
        let result = self
            .actor
            .apply_observation(observation)
            .map_err(MarketError::Invalid);
        if let Err(error) = &result {
            warn!(event = "market_observation_rejected", component = "market", error = %error, "market observation rejected");
        } else {
            debug!(
                event = "market_observation_applied",
                component = "market",
                "market observation applied"
            );
        }
        result
    }
}
