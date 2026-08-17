use tracing::{info, warn};

use crate::domain::market::ResolvedMarket;
use crate::domain::subscription::{ObservationSelector, SubscriptionId};

use super::super::{MarketApplication, MarketError};

impl MarketApplication {
    pub fn subscribe_static(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: ResolvedMarket,
    ) -> Result<(), MarketError> {
        self.subscribe_static_with_selectors(id, owner_id, market, Vec::new())
    }

    pub fn subscribe_static_with_selectors(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: ResolvedMarket,
        selectors: Vec<ObservationSelector>,
    ) -> Result<(), MarketError> {
        let subscription_id = id.0.clone();
        info!(event = "market_subscription_started", component = "market", subscription_id = %subscription_id, "static market subscription started");
        let result = if selectors.is_empty() {
            self.actor
                .subscribe_static(id, owner_id, market)
                .map_err(MarketError::InvalidSubscription)
        } else {
            self.actor
                .subscribe_static_with_selectors(id, owner_id, market, selectors)
                .map_err(MarketError::InvalidSubscription)
        };
        match &result {
            Ok(()) => {
                info!(event = "market_subscription_accepted", component = "market", subscription_id = %subscription_id, "static market subscription accepted")
            }
            Err(error) => {
                warn!(event = "market_subscription_rejected", component = "market", subscription_id = %subscription_id, error = %error, "static market subscription rejected")
            }
        }
        result
    }
}
