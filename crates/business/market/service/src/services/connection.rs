//! Runtime management for provider market connections.
//!
//! Connection state is deliberately kept outside `MarketActor`. The actor
//! owns business subscription intent and normalized market state; this
//! service owns provider subscription handles and feed lifecycle.

use std::collections::BTreeMap;

use super::feed::{MarketFeed, MarketOrderBookUpdate};
use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::MarketObservation;
use crate::domain::snapshot::SubscriptionState;
use crate::domain::subscriptions::SubscriptionId;

pub(crate) struct MarketBatch {
    pub observations: Vec<MarketObservation>,
    pub orderbooks: Vec<MarketOrderBookUpdate>,
}

pub(crate) struct MarketConnectionManager {
    feed: Box<dyn MarketFeed>,
    provider_subscriptions: BTreeMap<(SubscriptionId, String), (SubscriptionId, MarketDescriptor)>,
}

impl MarketConnectionManager {
    pub(crate) fn new(feed: Box<dyn MarketFeed>) -> Self {
        Self {
            feed,
            provider_subscriptions: BTreeMap::new(),
        }
    }

    pub(crate) fn start(&mut self) -> Result<(), String> {
        self.feed.start()
    }

    pub(crate) fn into_feed(self) -> Box<dyn MarketFeed> {
        self.feed
    }

    pub(crate) fn status(&self) -> FeedStatus {
        self.feed.status()
    }

    /// Make provider subscriptions match the business subscription state.
    pub(crate) fn reconcile(&mut self, subscriptions: &[SubscriptionState]) -> Result<(), String> {
        let mut desired = BTreeMap::<(SubscriptionId, String), MarketDescriptor>::new();
        for subscription in subscriptions {
            for (market_id, market) in &subscription.members {
                desired.insert((subscription.id.clone(), market_id.clone()), market.clone());
            }
        }

        let stale: Vec<_> = self
            .provider_subscriptions
            .keys()
            .filter(|key| !desired.contains_key(*key))
            .cloned()
            .collect();
        for key in stale {
            if let Some((provider_id, _)) = self.provider_subscriptions.get(&key) {
                self.feed.unsubscribe(provider_id)?;
            }
            self.provider_subscriptions.remove(&key);
        }

        let mut to_add = Vec::new();
        for (key, market) in desired {
            if let Some((provider_id, current_market)) = self.provider_subscriptions.get(&key) {
                if current_market == &market {
                    continue;
                }
                self.feed.unsubscribe(provider_id)?;
                self.provider_subscriptions.remove(&key);
            }
            to_add.push((key, market));
        }
        let provider_ids = self.feed.subscribe_many(
            &to_add
                .iter()
                .map(|(_, market)| market.clone())
                .collect::<Vec<_>>(),
        )?;
        for ((key, market), provider_id) in to_add.into_iter().zip(provider_ids) {
            self.provider_subscriptions
                .insert(key, (provider_id, market));
        }
        Ok(())
    }

    pub(crate) fn poll(&mut self) -> Result<MarketBatch, String> {
        let observations = self.feed.poll()?;
        let orderbooks = self.feed.poll_orderbooks()?;
        Ok(MarketBatch {
            observations,
            orderbooks,
        })
    }

    pub(crate) fn resync_orderbook(&mut self, market_id: &str) -> Result<(), String> {
        self.feed.resync_orderbook(market_id)
    }

    pub(crate) fn recover(&mut self) -> Result<(), String> {
        self.feed.recover()
    }
}

#[cfg(test)]
mod tests {
    use super::MarketConnectionManager;
    use crate::domain::freshness::FeedStatus;
    use crate::domain::market::MarketDescriptor;
    use crate::domain::observations::MarketObservation;
    use crate::domain::snapshot::SubscriptionState;
    use crate::domain::subscriptions::{SubscriptionId, SubscriptionMode};
    use crate::services::feed::MarketFeed;
    use std::sync::{Arc, Mutex};

    struct RecordingFeed {
        next_id: u64,
        operations: Arc<Mutex<Vec<String>>>,
    }

    impl MarketFeed for RecordingFeed {
        fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
            let id = SubscriptionId::new(format!("provider:{}", self.next_id))?;
            self.next_id += 1;
            self.operations
                .lock()
                .unwrap()
                .push(format!("subscribe:{}", market.source_symbol));
            Ok(id)
        }

        fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String> {
            self.operations
                .lock()
                .unwrap()
                .push(format!("unsubscribe:{}", subscription.0));
            Ok(())
        }

        fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
            Ok(Vec::new())
        }

        fn status(&self) -> FeedStatus {
            FeedStatus::Ready
        }
    }

    fn subscription(market: MarketDescriptor) -> SubscriptionState {
        SubscriptionState {
            id: SubscriptionId::new("subscription-1").unwrap(),
            owner_id: "strategy-1".into(),
            mode: SubscriptionMode::Static,
            query: None,
            members: [(market.market_id.clone(), market)].into_iter().collect(),
        }
    }

    #[test]
    fn descriptor_change_replaces_actual_provider_subscription() {
        let first =
            MarketDescriptor::new("market:btc", "instrument:btc", "binance", "spot", "BTCUSDT")
                .unwrap();
        let second = MarketDescriptor::new(
            "market:btc",
            "instrument:btc-v2",
            "binance",
            "spot",
            "BTCUSDT_PERPETUAL",
        )
        .unwrap();
        let operations = Arc::new(Mutex::new(Vec::new()));
        let feed = RecordingFeed {
            next_id: 1,
            operations: Arc::clone(&operations),
        };
        let mut manager = MarketConnectionManager::new(Box::new(feed));

        manager.reconcile(&[subscription(first)]).unwrap();
        manager.reconcile(&[subscription(second)]).unwrap();

        assert_eq!(
            *operations.lock().unwrap(),
            vec![
                "subscribe:BTCUSDT".to_string(),
                "unsubscribe:provider:1".to_string(),
                "subscribe:BTCUSDT_PERPETUAL".to_string(),
            ]
        );
    }
}
