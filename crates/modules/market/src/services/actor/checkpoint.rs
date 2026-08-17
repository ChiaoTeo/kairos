use std::collections::BTreeMap;

use kairos_primitives::{ActorId, Generation, Sequence};
use serde::{Deserialize, Serialize};

use crate::domain::{
    freshness::MarketFreshness,
    observation::{order_book::OrderBook, MarketObservation},
    subscription::SubscriptionState,
};

/// Replay-only recovery state for the single Market Actor.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub(crate) struct ReplayCheckpoint {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub views: BTreeMap<String, MarketObservation>,
    pub order_books: BTreeMap<String, OrderBook>,
    pub freshness: BTreeMap<String, MarketFreshness>,
    pub subscriptions: Vec<SubscriptionState>,
}

impl Default for ReplayCheckpoint {
    fn default() -> Self {
        Self {
            actor_id: ActorId::new("market").expect("valid market actor ID"),
            generation: Generation::default(),
            event_sequence: Sequence::default(),
            views: BTreeMap::new(),
            order_books: BTreeMap::new(),
            freshness: BTreeMap::new(),
            subscriptions: Vec::new(),
        }
    }
}
