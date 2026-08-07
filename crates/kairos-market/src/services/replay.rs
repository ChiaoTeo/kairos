//! Deterministic market feed used for warm-up, replay and actor tests.

use std::collections::VecDeque;

use crate::application::market::protocol::MarketFeed;
use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::MarketObservation;
use crate::domain::subscriptions::SubscriptionId;

pub struct ReplayMarketFeed {
    events: VecDeque<MarketObservation>,
    subscribed: bool,
    next_id: u64,
}

impl ReplayMarketFeed {
    pub fn new(events: impl IntoIterator<Item = MarketObservation>) -> Self {
        Self {
            events: events.into_iter().collect(),
            subscribed: false,
            next_id: 1,
        }
    }

    pub fn remaining(&self) -> usize {
        self.events.len()
    }
}

impl MarketFeed for ReplayMarketFeed {
    fn status(&self) -> FeedStatus {
        if self.subscribed {
            FeedStatus::Ready
        } else {
            FeedStatus::Disconnected
        }
    }

    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        market.validate()?;
        self.subscribed = true;
        let id = SubscriptionId::new(format!("replay:{}", self.next_id))?;
        self.next_id += 1;
        Ok(id)
    }

    fn unsubscribe(&mut self, _subscription: &SubscriptionId) -> Result<(), String> {
        self.subscribed = false;
        Ok(())
    }

    fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
        if !self.subscribed {
            return Err("replay feed is not subscribed".into());
        }
        Ok(self.events.drain(..).collect())
    }
}
