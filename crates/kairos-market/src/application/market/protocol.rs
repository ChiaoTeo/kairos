//! Protocols consumed by Market and owned by the Market application boundary.

use std::collections::BTreeMap;

use crate::domain::freshness::FeedStatus;
use crate::domain::market::{MarketDescriptor, MarketSelectionQuery};
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

pub trait ReferenceReader {
    fn select_markets(&self, query: &MarketSelectionQuery)
        -> Result<Vec<MarketDescriptor>, String>;
}

pub trait ReferenceChangeSource {
    fn next_change(&mut self) -> Result<Option<super::wire::ReferenceChangeNotice>, String>;
}

pub trait ReferenceSnapshotReader {
    fn read_markets(
        &mut self,
        notice: &super::wire::ReferenceChangeNotice,
    ) -> Result<Vec<MarketDescriptor>, String>;
}

pub trait MarketFeed {
    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String>;
    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String>;
    fn poll(&mut self) -> Result<Vec<MarketObservation>, String>;
    fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
        Ok(Vec::new())
    }
    fn status(&self) -> FeedStatus {
        FeedStatus::Disconnected
    }
    fn recover(&mut self) -> Result<(), String> {
        Ok(())
    }
}

pub trait MarketEventPublisher {
    fn publish(
        &mut self,
        event_sequence: u64,
        observation: &MarketObservation,
    ) -> Result<(), String>;
}

pub type MarketSelection = BTreeMap<String, MarketDescriptor>;
