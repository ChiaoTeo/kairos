//! Internal market connection capability.
//!
//! This seam belongs to the services layer.  It adapts concrete Integration,
//! replay, and composite connections to normalized Market inputs; it is not a
//! business application protocol.

use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::PriceLevel;
use crate::domain::subscriptions::SubscriptionId;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketOrderBookUpdate {
    pub market_id: String,
    pub instrument_id: String,
    pub first_sequence: u64,
    pub last_sequence: u64,
    pub event_time_unix_nanos: u64,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub snapshot: bool,
}

/// Internal connection seam for normalized market data.
pub trait MarketFeed: Send {
    fn start(&mut self) -> Result<(), String> {
        Ok(())
    }

    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String>;
    fn subscribe_many(
        &mut self,
        markets: &[MarketDescriptor],
    ) -> Result<Vec<SubscriptionId>, String> {
        markets
            .iter()
            .map(|market| self.subscribe(market))
            .collect()
    }
    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String>;
    fn poll(&mut self) -> Result<Vec<MarketObservation>, String>;
    fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
        Ok(Vec::new())
    }
    fn status(&self) -> FeedStatus {
        FeedStatus::Disconnected
    }
    fn resync_orderbook(&mut self, _market_id: &str) -> Result<(), String> {
        Err("market feed does not support single order book resync".into())
    }
    fn recover(&mut self) -> Result<(), String> {
        Ok(())
    }
}
