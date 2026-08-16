use std::collections::{BTreeMap, BTreeSet};

use crate::domain::events::OrderBookResyncRequired;
use crate::domain::events::{MarketChange, MarketEvent, MarketViewUpdate};
use crate::domain::freshness::{DataFreshnessStatus, FeedStatus, MarketFreshness};
use crate::domain::market::{MarketDescriptor, MarketSelectionQuery};
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::{OrderBook, OrderBookDelta};
use crate::domain::reference::ReferenceChanged;
use crate::domain::snapshot::{MarketSnapshot, ReconcileResult, SubscriptionState};
use crate::domain::source::{
    derive_readiness, SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceState,
    SourceStatus,
};
use crate::domain::subscriptions::{
    derive_subscription_status, selector_matches_observation, selector_matches_orderbook,
    validate_selectors, SubscriptionId, SubscriptionMemberRequirement, SubscriptionMemberStatus,
    SubscriptionMode,
};
use crate::services::messages::{
    ProviderSubscriptionId, SourceCommand, SourceInput, SourceRequestId,
};
use kairos_primitives::{ActorId, Generation, Sequence};
use tokio::sync::mpsc;

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

#[derive(Clone, Debug)]
struct DynamicIntent {
    owner_id: String,
    query: MarketSelectionQuery,
    selectors: Vec<String>,
    max_members: usize,
    members: BTreeMap<String, MarketDescriptor>,
    member_requirements: BTreeMap<String, SubscriptionMemberRequirement>,
}

pub struct MarketActor {
    actor_id: String,
    generation: Generation,
    event_sequence: Sequence,
    latest: BTreeMap<String, MarketObservation>,
    latest_views: BTreeMap<String, MarketObservation>,
    order_books: BTreeMap<String, OrderBook>,
    freshness: BTreeMap<String, MarketFreshness>,
    static_subscriptions: BTreeMap<SubscriptionId, SubscriptionState>,
    dynamic_intents: BTreeMap<SubscriptionId, DynamicIntent>,
    max_dynamic_members: usize,
    pending_changes: Vec<MarketChange>,
    reference_generation: Generation,
    reference_event_sequence: Sequence,
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
            latest: BTreeMap::new(),
            latest_views: BTreeMap::new(),
            order_books: BTreeMap::new(),
            freshness: BTreeMap::new(),
            static_subscriptions: BTreeMap::new(),
            dynamic_intents: BTreeMap::new(),
            max_dynamic_members,
            reference_generation: 0.into(),
            reference_event_sequence: 0.into(),
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
        snapshot: MarketSnapshot,
        max_dynamic_members: usize,
        source_input_capacity: usize,
    ) -> Result<Self, String> {
        if max_dynamic_members == 0 {
            return Err("max dynamic members must be positive".into());
        }
        let mut static_subscriptions = BTreeMap::new();
        let mut dynamic_intents = BTreeMap::new();
        for subscription in snapshot.subscriptions {
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
            actor_id: snapshot.actor_id.to_string(),
            generation: snapshot.generation,
            event_sequence: snapshot.event_sequence,
            latest: snapshot.latest,
            latest_views: snapshot.views,
            order_books: snapshot.order_books,
            freshness: snapshot.freshness,
            static_subscriptions,
            dynamic_intents,
            max_dynamic_members,
            pending_changes: Vec::new(),
            reference_generation: 0.into(),
            reference_event_sequence: 0.into(),
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
    ) -> Result<crate::services::sources::SourceHandle, String> {
        let mut attached = self
            .attached_sources
            .remove(source_id)
            .ok_or_else(|| format!("market source is not attached: {source_id}"))?;
        self.sources.remove(source_id);
        self.refresh_feed_status();
        Ok(crate::services::sources::SourceHandle {
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

    pub(crate) fn begin_orderbook_resync(
        &mut self,
        source_id: &SourceId,
        epoch: SourceEpoch,
        market_id: &kairos_primitives::MarketId,
        reason: String,
    ) -> Result<bool, String> {
        let source = self
            .sources
            .get_mut(source_id)
            .ok_or_else(|| format!("unknown market source: {source_id}"))?;
        if epoch != source.epoch {
            return Ok(false);
        }
        let first_request = !source.resyncing_markets.contains(market_id);
        if first_request {
            source.resyncing_markets.push(market_id.clone());
        }
        source.status = SourceStatus::WarmingUp;
        source.last_error = Some(reason.clone());
        if let Some(book) = self
            .order_books
            .get_mut(&format!("{source_id}:{market_id}"))
        {
            book.synchronized = false;
        }
        for freshness in self.freshness.values_mut().filter(|freshness| {
            freshness.source_id.eq_ignore_ascii_case(source_id.as_str())
                && freshness.market_id == *market_id
                && freshness.data_kind == "order_book"
        }) {
            freshness.status = DataFreshnessStatus::Stale;
        }
        self.refresh_feed_status();
        if first_request {
            let instrument_id = self
                .order_books
                .get(&format!("{source_id}:{market_id}"))
                .map(|book| book.instrument_id.clone())
                .unwrap_or_else(|| {
                    kairos_primitives::InstrumentId::new(market_id.as_str())
                        .expect("validated market id is a valid fallback instrument id")
                });
            self.event_sequence += 1;
            self.pending_changes.push(MarketChange {
                sequence: self.event_sequence,
                event: Some(MarketEvent::OrderBookResyncRequired(
                    OrderBookResyncRequired {
                        source_id: source_id.to_string(),
                        market_id: market_id.clone(),
                        instrument_id,
                        expected_sequence: self
                            .order_books
                            .get(&format!("{source_id}:{market_id}"))
                            .map(|book| book.sequence.get().saturating_add(1).into())
                            .unwrap_or_else(|| 0.into()),
                        observed_sequence: 0.into(),
                        reason,
                    },
                )),
                view: None,
            });
        }
        Ok(true)
    }

    pub(crate) fn complete_orderbook_resync(
        &mut self,
        source_id: &SourceId,
        epoch: SourceEpoch,
        market_id: &kairos_primitives::MarketId,
    ) -> Result<bool, String> {
        let source = self
            .sources
            .get_mut(source_id)
            .ok_or_else(|| format!("unknown market source: {source_id}"))?;
        if epoch != source.epoch {
            return Ok(false);
        }
        source
            .resyncing_markets
            .retain(|current| current != market_id);
        if source.resyncing_markets.is_empty() {
            source.status = SourceStatus::Ready;
            source.last_error = None;
        }
        self.refresh_feed_status();
        Ok(true)
    }

    fn refresh_feed_status(&mut self) {
        self.feed_status = match derive_readiness(self.sources.values()) {
            crate::domain::source::MarketReadiness::Ready => FeedStatus::Ready,
            crate::domain::source::MarketReadiness::Degraded => FeedStatus::Degraded,
            crate::domain::source::MarketReadiness::Stopped => FeedStatus::Disconnected,
            crate::domain::source::MarketReadiness::Starting => FeedStatus::WarmingUp,
        };
    }

    pub fn subscribe_static(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: MarketDescriptor,
    ) -> Result<(), String> {
        self.subscribe_static_with_selectors(id, owner_id, market, Vec::new())
    }

    pub fn subscribe_static_with_selectors(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: MarketDescriptor,
        selectors: Vec<String>,
    ) -> Result<(), String> {
        if self.static_subscriptions.contains_key(&id) || self.dynamic_intents.contains_key(&id) {
            return Err(format!("subscription id already exists: {}", id.0));
        }
        market.validate()?;
        validate_selectors(&selectors)?;
        let owner_id = owner_id.into();
        if owner_id.trim().is_empty() {
            return Err("subscription owner is required".into());
        }
        let mut members = BTreeMap::new();
        let market_id = market.market_id.to_string();
        members.insert(market_id.clone(), market);
        self.static_subscriptions.insert(
            id.clone(),
            SubscriptionState {
                id,
                owner_id,
                mode: SubscriptionMode::Static,
                query: None,
                selectors,
                members,
                member_requirements: [(market_id, SubscriptionMemberRequirement::Required)]
                    .into_iter()
                    .collect(),
                member_status: BTreeMap::new(),
                status: Default::default(),
            },
        );
        self.generation += 1;
        Ok(())
    }

    pub fn subscribe_dynamic(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        members: Vec<MarketDescriptor>,
    ) -> Result<ReconcileResult, String> {
        self.subscribe_dynamic_with_selectors(id, owner_id, query, members, Vec::new())
    }

    pub fn subscribe_dynamic_with_selectors(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        members: Vec<MarketDescriptor>,
        selectors: Vec<String>,
    ) -> Result<ReconcileResult, String> {
        if self.static_subscriptions.contains_key(&id) || self.dynamic_intents.contains_key(&id) {
            return Err(format!("subscription id already exists: {}", id.0));
        }
        let owner_id = owner_id.into();
        if owner_id.trim().is_empty() {
            return Err("subscription owner is required".into());
        }
        validate_selectors(&selectors)?;
        let selected = self.valid_members(query.clone(), members)?;
        if selected.len() > self.max_dynamic_members {
            return Err(format!(
                "dynamic subscription has {} members; limit is {}",
                selected.len(),
                self.max_dynamic_members
            ));
        }
        let previous = self
            .dynamic_intents
            .get(&id)
            .map(|intent| intent.members.clone())
            .unwrap_or_default();
        self.dynamic_intents.insert(
            id,
            DynamicIntent {
                owner_id,
                query,
                selectors,
                max_members: self.max_dynamic_members,
                members: selected.clone(),
                member_requirements: selected
                    .keys()
                    .map(|key| (key.clone(), SubscriptionMemberRequirement::Required))
                    .collect(),
            },
        );
        self.generation += 1;
        Ok(diff_members(&previous, &selected))
    }

    pub fn unsubscribe(&mut self, id: &SubscriptionId) -> bool {
        let removed = self.static_subscriptions.remove(id).is_some()
            || self.dynamic_intents.remove(id).is_some();
        if removed {
            self.generation += 1;
        }
        removed
    }

    pub fn unsubscribe_owned(
        &mut self,
        id: &SubscriptionId,
        owner_id: &str,
    ) -> Result<bool, String> {
        let existing_owner = self
            .static_subscriptions
            .get(id)
            .map(|subscription| subscription.owner_id.as_str())
            .or_else(|| {
                self.dynamic_intents
                    .get(id)
                    .map(|intent| intent.owner_id.as_str())
            });
        let Some(existing_owner) = existing_owner else {
            return Ok(false);
        };
        if existing_owner != owner_id {
            return Err(format!(
                "subscription {} belongs to a different owner",
                id.0
            ));
        }
        Ok(self.unsubscribe(id))
    }

    pub fn release_owner(&mut self, owner_id: &str) -> Vec<SubscriptionId> {
        let mut removed = self
            .static_subscriptions
            .iter()
            .filter(|(_, subscription)| subscription.owner_id == owner_id)
            .map(|(id, _)| id.clone())
            .chain(
                self.dynamic_intents
                    .iter()
                    .filter(|(_, intent)| intent.owner_id == owner_id)
                    .map(|(id, _)| id.clone()),
            )
            .collect::<Vec<_>>();
        removed.sort();
        removed.dedup();
        for id in &removed {
            self.static_subscriptions.remove(id);
            self.dynamic_intents.remove(id);
        }
        if !removed.is_empty() {
            self.generation += 1;
        }
        removed
    }

    pub fn set_member_requirement(
        &mut self,
        subscription_id: &SubscriptionId,
        member_id: impl Into<String>,
        requirement: SubscriptionMemberRequirement,
    ) -> Result<(), String> {
        let member_id = member_id.into();
        if let Some(subscription) = self.static_subscriptions.get_mut(subscription_id) {
            if !subscription.members.contains_key(&member_id) {
                return Err(format!("subscription member is not present: {member_id}"));
            }
            subscription
                .member_requirements
                .insert(member_id, requirement);
            self.generation += 1;
            return Ok(());
        }
        if let Some(intent) = self.dynamic_intents.get_mut(subscription_id) {
            if !intent.members.contains_key(&member_id) {
                return Err(format!("subscription member is not present: {member_id}"));
            }
            intent.member_requirements.insert(member_id, requirement);
            self.generation += 1;
            return Ok(());
        }
        Err(format!("subscription not found: {}", subscription_id.0))
    }

    pub fn reconcile_reference(
        &mut self,
        markets: Vec<MarketDescriptor>,
    ) -> Result<BTreeMap<SubscriptionId, ReconcileResult>, String> {
        let intents: Vec<_> = self
            .dynamic_intents
            .iter()
            .map(|(id, intent)| (id.clone(), intent.query.clone(), intent.max_members))
            .collect();
        let mut results = BTreeMap::new();
        for (id, query, max_members) in intents {
            let selected = self.valid_members(query, markets.clone())?;
            let intent = self.dynamic_intents.get_mut(&id).expect("intent exists");
            if selected.len() > max_members {
                results.insert(
                    id,
                    ReconcileResult {
                        rejected: Some(format!(
                            "dynamic subscription member limit exceeded: {} > {}",
                            selected.len(),
                            max_members
                        )),
                        ..Default::default()
                    },
                );
                continue;
            }
            let previous = intent.members.clone();
            intent.members = selected.clone();
            intent
                .member_requirements
                .retain(|member, _| selected.contains_key(member));
            for member in selected.keys() {
                intent
                    .member_requirements
                    .entry(member.clone())
                    .or_insert(SubscriptionMemberRequirement::Required);
            }
            let diff = diff_members(&previous, &selected);
            if diff.added.len() + diff.removed.len() + diff.changed.len() > 0 {
                self.generation += 1;
            }
            results.insert(id, diff);
        }
        Ok(results)
    }

    pub fn apply_reference_change(
        &mut self,
        change: ReferenceChanged,
    ) -> Result<BTreeMap<SubscriptionId, ReconcileResult>, String> {
        if change.generation < self.reference_generation
            || (change.generation == self.reference_generation
                && change.event_sequence <= self.reference_event_sequence)
        {
            return Ok(BTreeMap::new());
        }
        let result = self.reconcile_reference(change.markets)?;
        self.reference_generation = change.generation;
        self.reference_event_sequence = change.event_sequence;
        Ok(result)
    }

    pub fn apply_observation(&mut self, observation: MarketObservation) -> Result<u64, String> {
        observation.validate()?;
        if !self.observation_is_selected(&observation) {
            return Ok(self.event_sequence.get());
        }
        if self.pending_changes.len() >= Self::MAX_PENDING_EVENTS {
            return Err(format!(
                "market event backlog exceeded limit {}",
                Self::MAX_PENDING_EVENTS
            ));
        }
        // A canonical market may be observed from several independent
        // sources. Keep the compatibility "latest" projection source-aware
        // so one feed cannot silently replace another feed's value.
        let latest_key = format!("{}:{}", observation.source_id(), observation.market_id());
        let view = observation
            .view_key()
            .map_err(|error| format!("invalid market observation view: {error}"))?
            .as_str();
        let view_observation = observation.clone();
        let is_newer = self.latest.get(&latest_key).is_none_or(|current| {
            current.observed_at_unix_nanos() <= observation.observed_at_unix_nanos()
        });
        if is_newer {
            self.latest.insert(latest_key, observation.clone());
        }
        let view_is_newer = self.latest_views.get(&view).is_none_or(|current| {
            current.observed_at_unix_nanos() <= observation.observed_at_unix_nanos()
        });
        if view_is_newer {
            self.latest_views.insert(view, view_observation);
        }
        let freshness_key = observation
            .view_key()
            .map_err(|error| format!("invalid market freshness view: {error}"))?
            .as_str();
        let next_sequence = self.event_sequence.get().saturating_add(1);
        self.freshness.insert(
            freshness_key,
            MarketFreshness {
                source_id: observation.source_id().to_owned(),
                market_id: kairos_primitives::MarketId::new(observation.market_id().to_owned())
                    .expect("validated market observation market id"),
                data_kind: observation.view_kind().to_owned(),
                last_event_time_unix_nanos: observation.observed_at_unix_nanos(),
                last_received_time_unix_nanos: kairos_primitives::UnixNanos::new(now_unix_nanos()),
                event_sequence: kairos_primitives::Sequence::new(next_sequence),
                status: DataFreshnessStatus::Current,
            },
        );
        if self.feed_status == FeedStatus::WarmingUp {
            self.feed_status = FeedStatus::Ready;
        }
        self.event_sequence += 1;
        self.pending_changes.push(MarketChange {
            sequence: self.event_sequence,
            event: Some(MarketEvent::Observation(observation.clone())),
            view: Some(MarketViewUpdate::Observation(observation)),
        });
        Ok(self.event_sequence.get())
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

    pub fn apply_orderbook_snapshot(&mut self, book: OrderBook) -> Result<u64, String> {
        if !self.orderbook_is_selected(&book.market_id) {
            return Ok(self.event_sequence.get());
        }
        if self.pending_changes.len() >= Self::MAX_PENDING_EVENTS {
            return Err(format!(
                "market event backlog exceeded limit {}",
                Self::MAX_PENDING_EVENTS
            ));
        }
        let book_key = book.key();
        if let Some(current) = self.order_books.get(&book_key) {
            if current.source_id != book.source_id {
                return Err("order book snapshot source does not match existing book".into());
            }
            if current.instrument_id != book.instrument_id {
                return Err("order book snapshot identity does not match existing book".into());
            }
            if current.synchronized && book.sequence < current.sequence {
                return Err("stale order book snapshot".into());
            }
        }
        let source_id = book.source_id.clone();
        let market_id = book.market_id.clone();
        let event_time = book.event_time_unix_nanos;
        let synchronized = book.synchronized;
        self.order_books.insert(book_key, book.clone());
        self.event_sequence += 1;
        self.record_orderbook_freshness(
            &source_id,
            &market_id,
            event_time.get(),
            self.event_sequence.get(),
            synchronized,
        );
        self.pending_changes.push(MarketChange {
            sequence: self.event_sequence,
            event: Some(MarketEvent::OrderBookSnapshot(book.clone())),
            view: Some(MarketViewUpdate::OrderBook(book)),
        });
        Ok(self.event_sequence.get())
    }

    pub fn apply_orderbook_delta(&mut self, delta: OrderBookDelta) -> Result<u64, String> {
        if !self.orderbook_is_selected(&delta.market_id) {
            return Ok(self.event_sequence.get());
        }
        if self.pending_changes.len() >= Self::MAX_PENDING_EVENTS {
            return Err(format!(
                "market event backlog exceeded limit {}",
                Self::MAX_PENDING_EVENTS
            ));
        }
        let book = self
            .order_books
            .get_mut(&format!("{}:{}", delta.source_id, delta.market_id))
            .ok_or_else(|| "order book snapshot is required before delta".to_string())?;
        if delta.last_sequence <= book.sequence {
            return Ok(self.event_sequence.get());
        }
        let delta_for_event = delta.clone();
        book.apply_delta(delta)?;
        let freshness = (
            book.source_id.clone(),
            book.market_id.clone(),
            book.event_time_unix_nanos,
            book.sequence,
        );
        let event = book.clone();
        self.event_sequence += 1;
        self.record_orderbook_freshness(
            &freshness.0,
            &freshness.1,
            freshness.2.get(),
            self.event_sequence.get(),
            true,
        );
        self.pending_changes.push(MarketChange {
            sequence: self.event_sequence,
            event: Some(MarketEvent::OrderBookDelta(delta_for_event)),
            view: Some(MarketViewUpdate::OrderBook(event)),
        });
        Ok(self.event_sequence.get())
    }

    pub fn snapshot(&self) -> MarketSnapshot {
        let mut subscriptions = self
            .static_subscriptions
            .values()
            .map(|subscription| self.with_subscription_status(subscription))
            .collect::<Vec<_>>();
        subscriptions.extend(self.dynamic_intents.iter().map(|(id, intent)| {
            let member_status =
                self.subscription_member_status(id, &intent.members, &intent.member_requirements);
            SubscriptionState {
                id: id.clone(),
                owner_id: intent.owner_id.clone(),
                mode: SubscriptionMode::Dynamic,
                query: Some(intent.query.clone()),
                selectors: intent.selectors.clone(),
                members: intent.members.clone(),
                member_requirements: intent.member_requirements.clone(),
                status: derive_subscription_status(&intent.member_requirements, &member_status),
                member_status,
            }
        }));
        subscriptions.sort_by(|left, right| left.id.cmp(&right.id));
        MarketSnapshot {
            actor_id: ActorId::new(self.actor_id.clone()).expect("validated actor ID"),
            generation: self.generation,
            event_sequence: self.event_sequence,
            latest: self.latest.clone(),
            views: self.latest_views.clone(),
            order_books: self.order_books.clone(),
            freshness: self.freshness.clone(),
            subscriptions,
            sources: self.sources.clone(),
            readiness: derive_readiness(self.sources.values()),
            feed_status: self.feed_status,
        }
    }

    pub fn current_view(&self) -> crate::domain::snapshot::MarketCurrentView {
        let snapshot = self.snapshot();
        crate::domain::snapshot::MarketCurrentView {
            actor_id: snapshot.actor_id,
            generation: snapshot.generation,
            latest: snapshot.latest,
            views: snapshot.views,
            order_books: snapshot.order_books,
            freshness: snapshot
                .freshness
                .into_iter()
                .map(|(key, value)| {
                    (
                        key,
                        crate::domain::snapshot::MarketCurrentFreshness {
                            source_id: value.source_id,
                            market_id: value.market_id,
                            data_kind: value.data_kind,
                            last_event_time_unix_nanos: value.last_event_time_unix_nanos,
                            last_received_time_unix_nanos: value.last_received_time_unix_nanos,
                            status: value.status,
                        },
                    )
                })
                .collect(),
            subscriptions: snapshot.subscriptions,
            sources: snapshot.sources,
            readiness: snapshot.readiness,
            feed_status: snapshot.feed_status,
        }
    }

    fn with_subscription_status(&self, subscription: &SubscriptionState) -> SubscriptionState {
        let member_status = self.subscription_member_status(
            &subscription.id,
            &subscription.members,
            &subscription.member_requirements,
        );
        SubscriptionState {
            member_status: member_status.clone(),
            status: derive_subscription_status(&subscription.member_requirements, &member_status),
            ..subscription.clone()
        }
    }

    fn subscription_member_status(
        &self,
        subscription_id: &SubscriptionId,
        members: &BTreeMap<String, MarketDescriptor>,
        _requirements: &BTreeMap<String, SubscriptionMemberRequirement>,
    ) -> BTreeMap<String, SubscriptionMemberStatus> {
        members
            .iter()
            .map(|(market_id, market)| {
                let matches = self
                    .attached_sources
                    .iter()
                    .filter(|(_, source)| {
                        crate::application::source_accepts(&source.descriptor, market)
                    })
                    .collect::<Vec<_>>();
                let status = match matches.as_slice() {
                    [] => SubscriptionMemberStatus::Unavailable,
                    [(_, source)] => {
                        let confirmed = source
                            .confirmed
                            .contains_key(&(subscription_id.clone(), market_id.clone()));
                        let pending =
                            self.pending_for_subscription_market(subscription_id, market_id);
                        let source_status = self
                            .sources
                            .get(&source.descriptor.id)
                            .map(|state| state.status);
                        if confirmed {
                            match source_status {
                                Some(SourceStatus::Degraded | SourceStatus::Reconnecting) => {
                                    SubscriptionMemberStatus::Degraded
                                }
                                Some(SourceStatus::Stopped) => {
                                    SubscriptionMemberStatus::Unavailable
                                }
                                _ => SubscriptionMemberStatus::Ready,
                            }
                        } else if pending
                            || matches!(
                                source_status,
                                Some(SourceStatus::Starting | SourceStatus::WarmingUp)
                            )
                        {
                            SubscriptionMemberStatus::Pending
                        } else if matches!(
                            source_status,
                            Some(SourceStatus::Degraded | SourceStatus::Reconnecting)
                        ) {
                            SubscriptionMemberStatus::Degraded
                        } else {
                            SubscriptionMemberStatus::Unavailable
                        }
                    }
                    _ => SubscriptionMemberStatus::Rejected,
                };
                (market_id.clone(), status)
            })
            .collect()
    }

    fn pending_for_subscription_market(
        &self,
        subscription_id: &SubscriptionId,
        market_id: &str,
    ) -> bool {
        self.pending_source_requests.values().any(|pending| {
            matches!(pending, PendingSourceRequest::Subscribe { key, .. } if key.0 == *subscription_id && key.1 == market_id)
        })
    }

    fn valid_members(
        &self,
        query: MarketSelectionQuery,
        markets: Vec<MarketDescriptor>,
    ) -> Result<BTreeMap<String, MarketDescriptor>, String> {
        let mut selected = BTreeMap::new();
        for market in markets {
            market.validate()?;
            if query.matches(&market) {
                selected.insert(market.market_id.to_string(), market);
            }
        }
        Ok(selected)
    }

    fn observation_is_selected(&self, observation: &MarketObservation) -> bool {
        let qualifier = observation.view_qualifier();
        let kind = observation.view_kind();
        self.static_subscriptions.values().any(|subscription| {
            subscription.members.contains_key(observation.market_id())
                && selector_matches_observation(&subscription.selectors, kind, qualifier)
        }) || self.dynamic_intents.values().any(|intent| {
            intent.members.contains_key(observation.market_id())
                && selector_matches_observation(&intent.selectors, kind, qualifier)
        }) || (self.static_subscriptions.is_empty() && self.dynamic_intents.is_empty())
    }

    fn orderbook_is_selected(&self, market_id: &str) -> bool {
        self.static_subscriptions.values().any(|subscription| {
            subscription.members.contains_key(market_id)
                && selector_matches_orderbook(&subscription.selectors)
        }) || self.dynamic_intents.values().any(|intent| {
            intent.members.contains_key(market_id) && selector_matches_orderbook(&intent.selectors)
        }) || (self.static_subscriptions.is_empty() && self.dynamic_intents.is_empty())
    }

    fn record_orderbook_freshness(
        &mut self,
        source_id: &str,
        market_id: &str,
        event_time_unix_nanos: u64,
        sequence: u64,
        synchronized: bool,
    ) {
        self.freshness.insert(
            format!("{source_id}:{market_id}:order_book"),
            MarketFreshness {
                source_id: source_id.to_owned(),
                market_id: kairos_primitives::MarketId::new(market_id.to_owned())
                    .expect("validated order book market id"),
                data_kind: "order_book".into(),
                last_event_time_unix_nanos: kairos_primitives::UnixNanos::new(event_time_unix_nanos),
                last_received_time_unix_nanos: kairos_primitives::UnixNanos::new(now_unix_nanos()),
                event_sequence: kairos_primitives::Sequence::new(sequence),
                status: if synchronized {
                    DataFreshnessStatus::Current
                } else {
                    DataFreshnessStatus::Stale
                },
            },
        );
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos().min(u64::MAX as u128) as u64)
        .unwrap_or_default()
}

fn diff_members(
    previous: &BTreeMap<String, MarketDescriptor>,
    current: &BTreeMap<String, MarketDescriptor>,
) -> ReconcileResult {
    let previous_ids: BTreeSet<_> = previous.keys().cloned().collect();
    let current_ids: BTreeSet<_> = current.keys().cloned().collect();
    let changed: BTreeSet<_> = current_ids
        .intersection(&previous_ids)
        .filter(|market_id| previous.get(*market_id) != current.get(*market_id))
        .cloned()
        .collect();
    ReconcileResult {
        added: current_ids.difference(&previous_ids).cloned().collect(),
        removed: previous_ids.difference(&current_ids).cloned().collect(),
        changed: changed.iter().cloned().collect(),
        unchanged: current_ids
            .intersection(&previous_ids)
            .filter(|market_id| !changed.contains(*market_id))
            .cloned()
            .collect(),
        rejected: None,
    }
}
