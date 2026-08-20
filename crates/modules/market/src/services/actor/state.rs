use std::collections::BTreeMap;

use kairos_primitives::runtime::ActorId;
use kairos_primitives::time::{Generation, Sequence};

use super::ReplayCheckpoint;
use crate::domain::events::MarketChange;
use crate::domain::freshness::{FeedStatus, MarketFreshness};
use crate::domain::market::ResolvedMarket;
use crate::domain::observation::MarketObservation;
use crate::domain::observation::order_book::OrderBook;
use crate::domain::source::{SourceId, SourceState};
use crate::domain::subscription::{SubscriptionId, SubscriptionMode, SubscriptionState};
use crate::services::source::messages::SourceRequestId;
#[path = "freshness.rs"]
mod freshness_evaluation;
#[path = "observations/mod.rs"]
mod observations;
#[path = "sources.rs"]
pub(crate) mod sources;
#[path = "subscriptions.rs"]
mod subscriptions;
#[path = "universe/mod.rs"]
mod universe;

use sources::{AttachedSource, PendingSourceRequest};
use subscriptions::DynamicIntent;

pub struct MarketActor {
    pub(super) actor_id: String,
    pub(super) generation: Generation,
    pub(super) event_sequence: Sequence,
    pub(super) views: BTreeMap<String, MarketObservation>,
    pub(super) order_books: BTreeMap<String, OrderBook>,
    pub(super) freshness: BTreeMap<String, MarketFreshness>,
    static_subscriptions: BTreeMap<SubscriptionId, SubscriptionState>,
    dynamic_intents: BTreeMap<SubscriptionId, DynamicIntent>,
    pub(super) market_universe: BTreeMap<String, ResolvedMarket>,
    max_dynamic_members: usize,
    pub(super) pending_changes: Vec<MarketChange>,
    market_universe_generation: Generation,
    market_universe_event_sequence: Sequence,
    pub(super) feed_status: FeedStatus,
    pub(super) sources: BTreeMap<SourceId, SourceState>,
    /// Operational source state is owned by the same Actor as subscription
    /// intent and market state. The public application remains a facade and
    /// cannot become a second runtime/state owner.
    pub(crate) attached_sources: BTreeMap<SourceId, AttachedSource>,
    pub(crate) source_input_capacity: usize,
    pub(crate) next_source_input_index: usize,
    pub(crate) pending_source_requests: BTreeMap<SourceRequestId, PendingSourceRequest>,
    pub(crate) next_source_request_id: u64,
}

impl MarketActor {
    const MAX_PENDING_EVENTS: usize = 65_536;
    pub fn new(
        actor_id: impl Into<String>,
        max_dynamic_members: usize,
        source_input_capacity: usize,
    ) -> Result<Self, String> {
        let actor_id = actor_id.into();
        if actor_id.trim().is_empty() {
            return Err("market actor id is required".into());
        }
        if max_dynamic_members == 0 {
            return Err("max dynamic members must be positive".into());
        }
        Ok(Self {
            actor_id,
            generation: 0.into(),
            event_sequence: 0.into(),
            views: BTreeMap::new(),
            order_books: BTreeMap::new(),
            freshness: BTreeMap::new(),
            static_subscriptions: BTreeMap::new(),
            dynamic_intents: BTreeMap::new(),
            market_universe: BTreeMap::new(),
            max_dynamic_members,
            market_universe_generation: 0.into(),
            market_universe_event_sequence: 0.into(),
            feed_status: FeedStatus::Disconnected,
            sources: BTreeMap::new(),
            pending_changes: Vec::new(),
            attached_sources: BTreeMap::new(),
            source_input_capacity,
            next_source_input_index: 0,
            pending_source_requests: BTreeMap::new(),
            next_source_request_id: 1,
        })
    }

    pub(crate) fn restore(
        checkpoint: ReplayCheckpoint,
        max_dynamic_members: usize,
        source_input_capacity: usize,
    ) -> Result<Self, String> {
        if max_dynamic_members == 0 {
            return Err("max dynamic members must be positive".into());
        }
        let mut static_subscriptions = BTreeMap::new();
        let mut dynamic_intents = BTreeMap::new();
        for subscription in checkpoint.subscriptions {
            if subscription.mode == SubscriptionMode::Dynamic {
                let query = subscription
                    .query
                    .clone()
                    .ok_or("dynamic subscription has no query")?;
                dynamic_intents.insert(
                    subscription.id.clone(),
                    DynamicIntent {
                        owner_id: subscription.owner_id,
                        query,
                        selectors: subscription.selectors,
                        max_members: max_dynamic_members,
                        members: subscription.members,
                        member_requirements: subscription.member_requirements,
                    },
                );
            } else {
                static_subscriptions.insert(subscription.id.clone(), subscription);
            }
        }
        Ok(Self {
            actor_id: checkpoint.actor_id.to_string(),
            generation: checkpoint.generation,
            event_sequence: checkpoint.event_sequence,
            views: checkpoint.views,
            order_books: checkpoint.order_books,
            freshness: checkpoint.freshness,
            static_subscriptions,
            dynamic_intents,
            market_universe: BTreeMap::new(),
            max_dynamic_members,
            pending_changes: Vec::new(),
            market_universe_generation: 0.into(),
            market_universe_event_sequence: 0.into(),
            feed_status: FeedStatus::Disconnected,
            sources: BTreeMap::new(),
            attached_sources: BTreeMap::new(),
            source_input_capacity,
            next_source_input_index: 0,
            pending_source_requests: BTreeMap::new(),
            next_source_request_id: 1,
        })
    }

    pub(crate) fn checkpoint(&self) -> ReplayCheckpoint {
        ReplayCheckpoint {
            actor_id: ActorId::new(self.actor_id.clone()).expect("validated actor ID"),
            generation: self.generation,
            event_sequence: self.event_sequence,
            views: self.views.clone(),
            order_books: self.order_books.clone(),
            freshness: self.freshness.clone(),
            subscriptions: self.subscription_states(),
        }
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos().min(u64::MAX as u128) as u64)
        .unwrap_or_default()
}
