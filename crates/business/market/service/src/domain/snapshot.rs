use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use super::freshness::{FeedStatus, MarketFreshness};
use super::market::{MarketDescriptor, MarketSelectionQuery};
use super::observations::MarketObservation;
use super::orderbook::OrderBook;
use super::subscriptions::{SubscriptionId, SubscriptionMode};
use kairos_domain_types::{ActorId, Generation, Sequence};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SubscriptionState {
    pub id: SubscriptionId,
    pub owner_id: String,
    pub mode: SubscriptionMode,
    pub query: Option<MarketSelectionQuery>,
    #[serde(default)]
    pub selectors: Vec<String>,
    pub members: BTreeMap<String, MarketDescriptor>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSnapshot {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub latest: BTreeMap<String, MarketObservation>,
    /// Latest observation per source + market + observation kind.
    pub views: BTreeMap<String, MarketObservation>,
    pub order_books: BTreeMap<String, OrderBook>,
    pub freshness: BTreeMap<String, MarketFreshness>,
    pub subscriptions: Vec<SubscriptionState>,
    pub feed_status: FeedStatus,
}

impl Default for MarketSnapshot {
    fn default() -> Self {
        Self {
            actor_id: ActorId::new("market").expect("valid market actor ID"),
            generation: Generation::default(),
            event_sequence: Sequence::default(),
            latest: BTreeMap::new(),
            views: BTreeMap::new(),
            order_books: BTreeMap::new(),
            freshness: BTreeMap::new(),
            subscriptions: Vec::new(),
            feed_status: FeedStatus::default(),
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReconcileResult {
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub changed: Vec<String>,
    pub unchanged: Vec<String>,
    pub rejected: Option<String>,
}
