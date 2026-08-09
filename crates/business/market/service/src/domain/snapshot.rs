use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use super::freshness::FeedStatus;
use super::market::{MarketDescriptor, MarketSelectionQuery};
use super::observations::MarketObservation;
use super::orderbook::OrderBook;
use super::subscriptions::{SubscriptionId, SubscriptionMode};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SubscriptionState {
    pub id: SubscriptionId,
    pub owner_id: String,
    pub mode: SubscriptionMode,
    pub query: Option<MarketSelectionQuery>,
    pub members: BTreeMap<String, MarketDescriptor>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub latest: BTreeMap<String, MarketObservation>,
    /// Latest observation per source + market + observation kind.
    pub views: BTreeMap<String, MarketObservation>,
    pub order_books: BTreeMap<String, OrderBook>,
    pub subscriptions: Vec<SubscriptionState>,
    pub feed_status: FeedStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReconcileResult {
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub changed: Vec<String>,
    pub unchanged: Vec<String>,
}
