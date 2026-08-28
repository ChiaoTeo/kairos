use tracing::info;

use super::super::{MarketApplication, MarketError};
use crate::domain::subscription::{
    SubscriptionId, SubscriptionMemberRequirement, SubscriptionStatus,
};

impl MarketApplication {
    pub fn set_subscription_member_requirement(
        &mut self,
        subscription_id: &SubscriptionId,
        member_id: impl Into<String>,
        requirement: SubscriptionMemberRequirement,
    ) -> Result<(), MarketError> {
        self.actor
            .set_member_requirement(subscription_id, member_id, requirement)
            .map_err(MarketError::InvalidSubscription)
    }

    pub fn subscription_status(&self, id: &SubscriptionId) -> Option<SubscriptionStatus> {
        self.actor
            .current_view()
            .subscriptions
            .into_iter()
            .find(|subscription| subscription.id == *id)
            .map(|subscription| subscription.status)
    }

    pub fn unsubscribe(&mut self, id: &SubscriptionId) -> bool {
        let removed = self.actor.unsubscribe(id);
        info!(event = "market_subscription_removed", component = "market", subscription_id = %id, removed, "market subscription removal processed");
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
        info!(event = "market_subscription_removed", component = "market", subscription_id = %id, owner_id, removed, "owned market subscription removal processed");
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

    pub fn replace_subscription_members(
        &mut self,
        subscription_id: &SubscriptionId,
        markets: Vec<crate::ResolvedMarket>,
    ) -> Result<crate::ReconcileResult, MarketError> {
        self.actor
            .replace_subscription_members(subscription_id, markets)
            .map_err(MarketError::InvalidSubscription)
    }
}
