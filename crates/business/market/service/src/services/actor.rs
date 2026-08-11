use std::collections::{BTreeMap, BTreeSet};

use crate::domain::freshness::{DataFreshnessStatus, FeedStatus, MarketFreshness};
use crate::domain::market::{MarketDescriptor, MarketSelectionQuery};
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::{OrderBook, OrderBookDelta};
use crate::domain::reference::ReferenceChanged;
use crate::domain::snapshot::{MarketSnapshot, ReconcileResult, SubscriptionState};
use crate::domain::subscriptions::{
    selector_matches_observation, selector_matches_orderbook, validate_selectors, SubscriptionId,
    SubscriptionMode,
};
use kairos_domain_types::{ActorId, Generation, Sequence};

#[derive(Clone, Debug)]
struct DynamicIntent {
    owner_id: String,
    query: MarketSelectionQuery,
    selectors: Vec<String>,
    max_members: usize,
    members: BTreeMap<String, MarketDescriptor>,
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
    pending_events: Vec<(Sequence, MarketObservation)>,
    reference_generation: Generation,
    reference_event_sequence: Sequence,
    feed_status: FeedStatus,
}

impl MarketActor {
    const MAX_PENDING_EVENTS: usize = 65_536;
    pub fn new(actor_id: impl Into<String>, max_dynamic_members: usize) -> Result<Self, String> {
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
            pending_events: Vec::new(),
        })
    }

    pub(crate) fn set_feed_status(&mut self, status: FeedStatus) {
        self.feed_status = status;
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
        members.insert(market.market_id.to_string(), market);
        self.static_subscriptions.insert(
            id.clone(),
            SubscriptionState {
                id,
                owner_id,
                mode: SubscriptionMode::Static,
                query: None,
                selectors,
                members,
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
        if self.pending_events.len() >= Self::MAX_PENDING_EVENTS {
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
                market_id: kairos_domain_types::MarketId::new(observation.market_id().to_owned())
                    .expect("validated market observation market id"),
                data_kind: observation.view_kind().to_owned(),
                last_event_time_unix_nanos: observation.observed_at_unix_nanos(),
                last_received_time_unix_nanos: kairos_domain_types::UnixNanos::new(now_unix_nanos()),
                event_sequence: kairos_domain_types::Sequence::new(next_sequence),
                status: DataFreshnessStatus::Current,
            },
        );
        if self.feed_status == FeedStatus::WarmingUp {
            self.feed_status = FeedStatus::Ready;
        }
        self.event_sequence += 1;
        self.pending_events.push((self.event_sequence, observation));
        Ok(self.event_sequence.get())
    }

    pub fn drain_events(&mut self) -> Vec<(Sequence, MarketObservation)> {
        std::mem::take(&mut self.pending_events)
    }

    pub fn drain_events_limited(&mut self, limit: usize) -> Vec<(Sequence, MarketObservation)> {
        if limit == 0 {
            return Vec::new();
        }
        let count = self.pending_events.len().min(limit);
        self.pending_events.drain(..count).collect()
    }

    pub fn apply_orderbook_snapshot(&mut self, book: OrderBook) -> Result<u64, String> {
        if !self.orderbook_is_selected(&book.market_id) {
            return Ok(self.event_sequence.get());
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
        let sequence = book.sequence;
        let synchronized = book.synchronized;
        self.order_books.insert(book_key, book);
        self.event_sequence += 1;
        self.record_orderbook_freshness(
            &source_id,
            &market_id,
            event_time.get(),
            sequence.get(),
            synchronized,
        );
        Ok(self.event_sequence.get())
    }

    pub fn apply_orderbook_delta(&mut self, delta: OrderBookDelta) -> Result<u64, String> {
        if !self.orderbook_is_selected(&delta.market_id) {
            return Ok(self.event_sequence.get());
        }
        let book = self
            .order_books
            .get_mut(&format!("{}:{}", delta.source_id, delta.market_id))
            .ok_or_else(|| "order book snapshot is required before delta".to_string())?;
        book.apply_delta(delta)?;
        let freshness = (
            book.source_id.clone(),
            book.market_id.clone(),
            book.event_time_unix_nanos,
            book.sequence,
        );
        self.event_sequence += 1;
        self.record_orderbook_freshness(
            &freshness.0,
            &freshness.1,
            freshness.2.get(),
            freshness.3.get(),
            true,
        );
        Ok(self.event_sequence.get())
    }

    pub fn snapshot(&self) -> MarketSnapshot {
        let mut subscriptions = self
            .static_subscriptions
            .values()
            .cloned()
            .collect::<Vec<_>>();
        subscriptions.extend(
            self.dynamic_intents
                .iter()
                .map(|(id, intent)| SubscriptionState {
                    id: id.clone(),
                    owner_id: intent.owner_id.clone(),
                    mode: SubscriptionMode::Dynamic,
                    query: Some(intent.query.clone()),
                    selectors: intent.selectors.clone(),
                    members: intent.members.clone(),
                }),
        );
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
            feed_status: self.feed_status,
        }
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
                market_id: kairos_domain_types::MarketId::new(market_id.to_owned())
                    .expect("validated order book market id"),
                data_kind: "order_book".into(),
                last_event_time_unix_nanos: kairos_domain_types::UnixNanos::new(event_time_unix_nanos),
                last_received_time_unix_nanos: kairos_domain_types::UnixNanos::new(now_unix_nanos()),
                event_sequence: kairos_domain_types::Sequence::new(sequence),
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
