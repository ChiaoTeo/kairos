use tracing::info;

use crate::domain::subscription::SubscriptionId;

use super::super::{MarketApplication, MarketError};

impl MarketApplication {
    pub fn unsubscribe(&mut self, id: &SubscriptionId) -> bool {
        let removed = self.actor.unsubscribe(id);
        info!(event = "market_subscription_removed", component = "market", subscription_id = %id.0, removed, "market subscription removal processed");
        removed
    }

    pub fn unsubscribe_owned(
        &mut self,
        id: &SubscriptionId,
        owner_id: &str,
    ) -> Result<bool, MarketError> {
        let removed = self
            .actor
            .unsubscribe_owned(id, owner_id)
            .map_err(MarketError::InvalidSubscription)?;
        info!(event = "market_subscription_removed", component = "market", subscription_id = %id.0, owner_id, removed, "owned market subscription removal processed");
        Ok(removed)
    }

    pub fn release_subscription_owner(&mut self, owner_id: &str) -> Vec<SubscriptionId> {
        let removed = self.actor.release_owner(owner_id);
        info!(
            event = "market_subscription_owner_released",
            component = "market",
            owner_id,
            removed_count = removed.len(),
            "market subscription owner released"
        );
        removed
    }
}
