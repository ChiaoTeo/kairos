use std::collections::{BTreeMap, BTreeSet};

use crate::domain::freshness::FeedStatus;
use crate::domain::market::{MarketDescriptor, MarketSelectionQuery};
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::{OrderBook, OrderBookDelta};
use crate::domain::reference::ReferenceChanged;
use crate::domain::snapshot::{MarketSnapshot, ReconcileResult, SubscriptionState};
use crate::domain::subscriptions::{SubscriptionId, SubscriptionMode};

#[derive(Clone, Debug)]
struct DynamicIntent {
    owner_id: String,
    query: MarketSelectionQuery,
    max_members: usize,
    members: BTreeMap<String, MarketDescriptor>,
}

pub struct MarketActor {
    actor_id: String,
    generation: u64,
    event_sequence: u64,
    latest: BTreeMap<String, MarketObservation>,
    latest_views: BTreeMap<String, MarketObservation>,
    order_books: BTreeMap<String, OrderBook>,
    static_subscriptions: BTreeMap<SubscriptionId, SubscriptionState>,
    dynamic_intents: BTreeMap<SubscriptionId, DynamicIntent>,
    max_dynamic_members: usize,
    pending_events: Vec<(u64, MarketObservation)>,
    reference_generation: u64,
    reference_event_sequence: u64,
    feed_status: FeedStatus,
}

impl MarketActor {
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
            generation: 0,
            event_sequence: 0,
            latest: BTreeMap::new(),
            latest_views: BTreeMap::new(),
            order_books: BTreeMap::new(),
            static_subscriptions: BTreeMap::new(),
            dynamic_intents: BTreeMap::new(),
            max_dynamic_members,
            reference_generation: 0,
            reference_event_sequence: 0,
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
        if self.static_subscriptions.contains_key(&id) || self.dynamic_intents.contains_key(&id) {
            return Err(format!("subscription id already exists: {}", id.0));
        }
        market.validate()?;
        let owner_id = owner_id.into();
        if owner_id.trim().is_empty() {
            return Err("subscription owner is required".into());
        }
        let mut members = BTreeMap::new();
        members.insert(market.market_id.clone(), market);
        self.static_subscriptions.insert(
            id.clone(),
            SubscriptionState {
                id,
                owner_id,
                mode: SubscriptionMode::Static,
                query: None,
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
        if self.static_subscriptions.contains_key(&id) || self.dynamic_intents.contains_key(&id) {
            return Err(format!("subscription id already exists: {}", id.0));
        }
        let owner_id = owner_id.into();
        if owner_id.trim().is_empty() {
            return Err("subscription owner is required".into());
        }
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
                results.insert(id, ReconcileResult::default());
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
        if observation.market_id().trim().is_empty() {
            return Err("observation market id is required".into());
        }
        let market_id = observation.market_id().to_string();
        let view = observation
            .view_key()
            .map_err(|error| format!("invalid market observation view: {error}"))?
            .as_str();
        let view_observation = observation.clone();
        self.latest.insert(market_id.clone(), observation);
        self.latest_views.insert(view, view_observation);
        if self.feed_status == FeedStatus::WarmingUp {
            self.feed_status = FeedStatus::Ready;
        }
        self.event_sequence += 1;
        let value = self
            .latest
            .get(&market_id)
            .expect("observation exists")
            .clone();
        self.pending_events.push((self.event_sequence, value));
        Ok(self.event_sequence)
    }

    pub fn drain_events(&mut self) -> Vec<(u64, MarketObservation)> {
        std::mem::take(&mut self.pending_events)
    }

    pub fn drain_events_limited(&mut self, limit: usize) -> Vec<(u64, MarketObservation)> {
        if limit == 0 {
            return Vec::new();
        }
        let count = self.pending_events.len().min(limit);
        self.pending_events.drain(..count).collect()
    }

    pub fn apply_orderbook_snapshot(&mut self, book: OrderBook) -> Result<u64, String> {
        self.order_books.insert(book.market_id.clone(), book);
        self.event_sequence += 1;
        Ok(self.event_sequence)
    }

    pub fn apply_orderbook_delta(&mut self, delta: OrderBookDelta) -> Result<u64, String> {
        let book = self
            .order_books
            .get_mut(&delta.market_id)
            .ok_or_else(|| "order book snapshot is required before delta".to_string())?;
        book.apply_delta(delta)?;
        self.event_sequence += 1;
        Ok(self.event_sequence)
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
                    members: intent.members.clone(),
                }),
        );
        subscriptions.sort_by(|left, right| left.id.cmp(&right.id));
        MarketSnapshot {
            actor_id: self.actor_id.clone(),
            generation: self.generation,
            event_sequence: self.event_sequence,
            latest: self.latest.clone(),
            views: self.latest_views.clone(),
            order_books: self.order_books.clone(),
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
                selected.insert(market.market_id.clone(), market);
            }
        }
        Ok(selected)
    }
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
    }
}
