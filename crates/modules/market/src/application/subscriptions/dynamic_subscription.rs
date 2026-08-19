use tracing::{info, warn};

use super::super::{MarketApplication, MarketError};
use crate::domain::market::{MarketSelectionQuery, ResolvedMarket};
use crate::domain::subscription::{ObservationSelector, ReconcileResult, SubscriptionId};

impl MarketApplication {
    pub fn subscribe_dynamic(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        markets: Vec<ResolvedMarket>,
    ) -> Result<ReconcileResult, MarketError> {
        self.subscribe_dynamic_with_selectors(id, owner_id, query, markets, Vec::new())
    }

    pub fn subscribe_dynamic_with_selectors(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        markets: Vec<ResolvedMarket>,
        selectors: Vec<ObservationSelector>,
    ) -> Result<ReconcileResult, MarketError> {
        let subscription_id = id.to_string();
        let market_count = markets.len();
        info!(event = "market_dynamic_subscription_started", component = "market", subscription_id = %subscription_id, market_count, "dynamic market subscription started");
        let result = if selectors.is_empty() {
            self.actor
                .subscribe_dynamic(id, owner_id, query, markets)
                .map_err(MarketError::InvalidSubscription)
        } else {
            self.actor
                .subscribe_dynamic_with_selectors(id, owner_id, query, markets, selectors)
                .map_err(MarketError::InvalidSubscription)
        };
        match &result {
            Ok(reconcile) => {
                info!(event = "market_dynamic_subscription_accepted", component = "market", subscription_id = %subscription_id, added = reconcile.added.len(), removed = reconcile.removed.len(), "dynamic market subscription accepted")
            },
            Err(error) => {
                warn!(event = "market_dynamic_subscription_rejected", component = "market", subscription_id = %subscription_id, error = %error, "dynamic market subscription rejected")
            },
        }
        result
    }
}
