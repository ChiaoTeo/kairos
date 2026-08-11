//! Application-owned capabilities supplied by composition.

use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::PriceLevel;
use crate::domain::subscriptions::SubscriptionId;
use kairos_domain_types::{Exchange, InstrumentId, MarketId, Sequence, UnixNanos};

/// A provider route exposed for startup validation and diagnostics.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct MarketFeedRoute {
    pub source_id: Option<String>,
    pub exchange_id: Exchange,
    pub market_type: String,
    pub asset_type: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketOrderBookUpdate {
    pub source_id: String,
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub first_sequence: Sequence,
    pub last_sequence: Sequence,
    pub event_time_unix_nanos: UnixNanos,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub snapshot: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketDataKey {
    pub source_id: String,
    pub market_id: MarketId,
}

impl MarketDataKey {
    pub fn new(source_id: impl Into<String>, market_id: MarketId) -> Result<Self, String> {
        let source_id = source_id.into();
        if source_id.trim().is_empty() {
            return Err("market data source id is required".into());
        }
        Ok(Self {
            source_id,
            market_id,
        })
    }
}

pub trait MarketFeed: Send {
    fn start(&mut self) -> Result<(), String> {
        Ok(())
    }

    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String>;

    fn subscribe_many(
        &mut self,
        markets: &[MarketDescriptor],
    ) -> Result<Vec<SubscriptionId>, String> {
        let mut subscriptions = Vec::with_capacity(markets.len());
        for market in markets {
            match self.subscribe(market) {
                Ok(subscription) => subscriptions.push(subscription),
                Err(error) => {
                    for subscription in &subscriptions {
                        let _ = self.unsubscribe(subscription);
                    }
                    return Err(format!("market batch subscription rolled back: {error}"));
                }
            }
        }
        Ok(subscriptions)
    }

    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String>;
    fn poll(&mut self) -> Result<Vec<MarketObservation>, String>;

    fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
        Ok(Vec::new())
    }

    fn status(&self) -> FeedStatus {
        FeedStatus::Disconnected
    }

    fn resync_orderbook(&mut self, _key: &MarketDataKey) -> Result<(), String> {
        Err("market feed does not support single order book resync".into())
    }

    fn recover(&mut self) -> Result<(), String> {
        Ok(())
    }

    /// Number of buffered observations when the feed has a finite source,
    /// such as replay. Live feeds return the default sentinel value.
    fn remaining(&self) -> usize {
        0
    }

    /// Whether a finite feed has emitted all source observations. Live feeds
    /// keep the default `false` value and continue reconnecting indefinitely.
    fn is_complete(&self) -> bool {
        false
    }

    /// Routes known by a composed feed. A single provider connection has no
    /// route directory and therefore returns an empty collection.
    fn configured_routes(&self) -> Vec<MarketFeedRoute> {
        Vec::new()
    }
}
