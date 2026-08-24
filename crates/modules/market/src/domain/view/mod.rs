use std::collections::BTreeMap;

use kairos_primitives::market::Provider;
use kairos_primitives::runtime::ActorId;
use kairos_primitives::time::Generation;
use serde::{Deserialize, Serialize};

use super::freshness::{DataFreshnessStatus, FeedStatus};
use super::observation::order_book::OrderBook;
use super::observation::{MarketObservation, ObservationKind, ObservationScope};
use super::source::MarketReadiness;
use super::subscription::SubscriptionState;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketViewFreshness {
    pub provider: Provider,
    pub scope: ObservationScope,
    pub data_kind: ObservationKind,
    pub last_event_time_unix_nanos: kairos_primitives::time::UnixNanos,
    pub last_received_time_unix_nanos: kairos_primitives::time::UnixNanos,
    pub status: DataFreshnessStatus,
}

/// Pure current state. Stream positions and replay cursors are deliberately
/// excluded; those belong to events and ReplayCheckpoint respectively.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketView {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub views: BTreeMap<String, MarketObservation>,
    pub order_books: BTreeMap<String, OrderBook>,
    pub freshness: BTreeMap<String, MarketViewFreshness>,
    pub subscriptions: Vec<SubscriptionState>,
    pub readiness: MarketReadiness,
    pub feed_status: FeedStatus,
}
