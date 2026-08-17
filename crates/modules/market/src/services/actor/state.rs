use std::collections::BTreeMap;

use super::ReplayCheckpoint;
use crate::domain::events::{MarketChange, MarketEvent, MarketViewUpdate};
use crate::domain::freshness::{DataFreshnessStatus, FeedStatus, MarketFreshness};
use crate::domain::market::ResolvedMarket;
use crate::domain::observation::order_book::OrderBook;
use crate::domain::observation::MarketObservation;
use crate::domain::source::{
    derive_readiness, SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceState,
    SourceStatus,
};
use crate::domain::subscription::{SubscriptionId, SubscriptionMode, SubscriptionState};
use crate::services::source::messages::{
    ProviderSubscriptionId, SourceCommand, SourceInput, SourceRequestId,
};
use kairos_primitives::{ActorId, Generation, Sequence};
use tokio::sync::mpsc;

#[path = "observations/mod.rs"]
mod observations;
#[path = "subscriptions.rs"]
mod subscriptions;
#[path = "universe/mod.rs"]
mod universe;

use subscriptions::DynamicIntent;

pub(crate) type BusinessSubscriptionKey = (SubscriptionId, String);

pub(crate) struct AttachedSource {
    pub(crate) descriptor: SourceDescriptor,
    pub(crate) commands: mpsc::Sender<SourceCommand>,
    pub(crate) inputs: mpsc::Receiver<SourceInput>,
    pub(crate) task: Option<tokio::task::JoinHandle<()>>,
    pub(crate) confirmed: BTreeMap<BusinessSubscriptionKey, ProviderSubscriptionId>,
}

pub(crate) enum PendingSourceRequest {
    Subscribe {
        source_id: SourceId,
        key: BusinessSubscriptionKey,
    },
    Unsubscribe {
        source_id: SourceId,
        key: BusinessSubscriptionKey,
    },
    ResyncOrderBook {
        source_id: SourceId,
        market_id: kairos_primitives::MarketId,
    },
}

impl PendingSourceRequest {
    pub(crate) fn source_id(&self) -> &SourceId {
        match self {
            Self::Subscribe { source_id, .. }
            | Self::Unsubscribe { source_id, .. }
            | Self::ResyncOrderBook { source_id, .. } => source_id,
        }
    }
}

pub struct MarketActor {
    actor_id: String,
    generation: Generation,
    event_sequence: Sequence,
    views: BTreeMap<String, MarketObservation>,
    order_books: BTreeMap<String, OrderBook>,
    freshness: BTreeMap<String, MarketFreshness>,
    static_subscriptions: BTreeMap<SubscriptionId, SubscriptionState>,
    dynamic_intents: BTreeMap<SubscriptionId, DynamicIntent>,
    market_universe: BTreeMap<String, ResolvedMarket>,
    max_dynamic_members: usize,
    pending_changes: Vec<MarketChange>,
    market_universe_generation: Generation,
    market_universe_event_sequence: Sequence,
    feed_status: FeedStatus,
    sources: BTreeMap<SourceId, SourceState>,
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

    pub(crate) fn register_source(&mut self, descriptor: SourceDescriptor) -> Result<(), String> {
        if self.sources.contains_key(&descriptor.id) {
            return Err(format!("market source already exists: {}", descriptor.id));
        }
        self.sources
            .insert(descriptor.id.clone(), SourceState::starting(descriptor));
        self.refresh_feed_status();
        Ok(())
    }

    pub(crate) fn take_source_handle(
        &mut self,
        source_id: &SourceId,
    ) -> Result<crate::services::source::SourceHandle, String> {
        let mut attached = self
            .attached_sources
            .remove(source_id)
            .ok_or_else(|| format!("market source is not attached: {source_id}"))?;
        self.sources.remove(source_id);
        self.refresh_feed_status();
        Ok(crate::services::source::SourceHandle {
            descriptor: attached.descriptor,
            commands: attached.commands,
            inputs: attached.inputs,
            task: attached
                .task
                .take()
                .ok_or_else(|| format!("market source task is missing: {source_id}"))?,
        })
    }

    pub(crate) fn source_is_stopped(&self, source_id: &SourceId) -> bool {
        self.sources
            .get(source_id)
            .is_some_and(|source| source.status == SourceStatus::Stopped)
    }

    pub(crate) fn source_command_closed(&self, source_id: &SourceId) -> bool {
        self.attached_sources
            .get(source_id)
            .is_some_and(|source| source.commands.is_closed())
    }

    pub(crate) fn apply_source_status(
        &mut self,
        source_id: &SourceId,
        epoch: SourceEpoch,
        status: SourceStatus,
        error: Option<String>,
    ) -> Result<bool, String> {
        let source = self
            .sources
            .get_mut(source_id)
            .ok_or_else(|| format!("unknown market source: {source_id}"))?;
        let changed = source.change_status(epoch, status, error);
        if changed {
            self.refresh_feed_status();
        }
        Ok(changed)
    }

    pub(crate) fn apply_source_failure(
        &mut self,
        source_id: &SourceId,
        epoch: SourceEpoch,
        kind: SourceFailureKind,
        error: String,
    ) -> Result<bool, String> {
        let source = self
            .sources
            .get_mut(source_id)
            .ok_or_else(|| format!("unknown market source: {source_id}"))?;
        let changed = source.fail(epoch, kind, error);
        if changed {
            self.refresh_feed_status();
        }
        Ok(changed)
    }

    fn refresh_feed_status(&mut self) {
        self.feed_status = match derive_readiness(self.sources.values()) {
            crate::domain::source::MarketReadiness::Ready => FeedStatus::Ready,
            crate::domain::source::MarketReadiness::Degraded => FeedStatus::Degraded,
            crate::domain::source::MarketReadiness::Stopped => FeedStatus::Disconnected,
            crate::domain::source::MarketReadiness::Starting => FeedStatus::WarmingUp,
        };
    }

    pub(crate) fn drain_events(&mut self) -> Vec<(Sequence, MarketEvent)> {
        self.drain_changes()
            .into_iter()
            .filter_map(|change| change.event.map(|event| (change.sequence, event)))
            .collect()
    }

    pub(crate) fn drain_changes(&mut self) -> Vec<MarketChange> {
        std::mem::take(&mut self.pending_changes)
    }

    /// Re-evaluate receive-time freshness without performing external I/O.
    /// This is deliberately timer driven while live ingest remains wake
    /// driven by SourceInput.
    pub fn evaluate_freshness(&mut self, now_unix_nanos: u64, max_age_nanos: u64) {
        let mut changed = Vec::new();
        for freshness in self.freshness.values_mut() {
            let next_status = if now_unix_nanos
                .saturating_sub(freshness.last_received_time_unix_nanos.get())
                > max_age_nanos
            {
                DataFreshnessStatus::Stale
            } else {
                DataFreshnessStatus::Current
            };
            if freshness.status != next_status {
                freshness.status = next_status;
                self.event_sequence += 1;
                freshness.event_sequence = self.event_sequence;
                changed.push((self.event_sequence, freshness.clone()));
            }
        }
        for (sequence, freshness) in changed {
            if self.pending_changes.len() >= Self::MAX_PENDING_EVENTS {
                break;
            }
            self.pending_changes.push(MarketChange {
                sequence,
                event: None,
                view: Some(MarketViewUpdate::Freshness(freshness)),
            });
        }
    }

    pub(crate) fn drain_events_limited(&mut self, limit: usize) -> Vec<(Sequence, MarketEvent)> {
        if limit == 0 {
            return Vec::new();
        }
        self.drain_changes_limited(limit)
            .into_iter()
            .filter_map(|change| change.event.map(|event| (change.sequence, event)))
            .collect()
    }

    pub(crate) fn drain_changes_limited(&mut self, limit: usize) -> Vec<MarketChange> {
        if limit == 0 {
            return Vec::new();
        }
        let count = self.pending_changes.len().min(limit);
        self.pending_changes.drain(..count).collect()
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

    pub fn current_view(&self) -> crate::domain::view::MarketView {
        crate::domain::view::MarketView {
            actor_id: ActorId::new(self.actor_id.clone()).expect("validated actor ID"),
            generation: self.generation,
            views: self.views.clone(),
            order_books: self.order_books.clone(),
            freshness: self
                .freshness
                .iter()
                .map(|(key, value)| {
                    (
                        key.clone(),
                        crate::domain::view::MarketViewFreshness {
                            source_id: value.source_id.clone(),
                            market_id: value.market_id.clone(),
                            data_kind: value.data_kind.clone(),
                            last_event_time_unix_nanos: value.last_event_time_unix_nanos,
                            last_received_time_unix_nanos: value.last_received_time_unix_nanos,
                            status: value.status,
                        },
                    )
                })
                .collect(),
            subscriptions: self.subscription_states(),
            sources: self.sources.clone(),
            readiness: derive_readiness(self.sources.values()),
            feed_status: self.feed_status,
        }
    }

    pub(crate) fn event_sequence(&self) -> Sequence {
        self.event_sequence
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos().min(u64::MAX as u128) as u64)
        .unwrap_or_default()
}
