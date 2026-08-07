use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::application::market::protocol::{MarketEventPublisher, MarketFeed};
use crate::domain::freshness::FeedStatus;
use crate::domain::market::{MarketDescriptor, MarketSelectionQuery};
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::{OrderBook, OrderBookDelta};
use crate::domain::reference::ReferenceChanged;
use crate::domain::subscriptions::{SubscriptionId, SubscriptionMode};

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
    pub order_books: BTreeMap<String, OrderBook>,
    pub subscriptions: Vec<SubscriptionState>,
    pub feed_status: FeedStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReconcileResult {
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub unchanged: Vec<String>,
}

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
    order_books: BTreeMap<String, OrderBook>,
    static_subscriptions: BTreeMap<SubscriptionId, SubscriptionState>,
    dynamic_intents: BTreeMap<SubscriptionId, DynamicIntent>,
    max_dynamic_members: usize,
    feed: Option<Box<dyn MarketFeed>>,
    provider_subscriptions: BTreeMap<(SubscriptionId, String), SubscriptionId>,
    event_publisher: Option<Box<dyn MarketEventPublisher>>,
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
            order_books: BTreeMap::new(),
            static_subscriptions: BTreeMap::new(),
            dynamic_intents: BTreeMap::new(),
            max_dynamic_members,
            reference_generation: 0,
            reference_event_sequence: 0,
            feed_status: FeedStatus::Disconnected,
            feed: None,
            provider_subscriptions: BTreeMap::new(),
            event_publisher: None,
        })
    }

    pub fn actor_id(&self) -> &str {
        &self.actor_id
    }

    pub fn attach_feed(&mut self, feed: Box<dyn MarketFeed>) {
        self.feed_status = feed.status();
        self.feed = Some(feed);
    }

    pub fn feed_status(&self) -> FeedStatus {
        self.feed_status
    }

    pub fn attach_event_publisher(&mut self, publisher: Box<dyn MarketEventPublisher>) {
        self.event_publisher = Some(publisher);
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
        let provider_id = if let Some(feed) = self.feed.as_mut() {
            Some(feed.subscribe(members.values().next().expect("member exists"))?)
        } else {
            None
        };
        if let Some(provider_id) = provider_id {
            self.provider_subscriptions.insert(
                (id.clone(), members.keys().next().unwrap().clone()),
                provider_id,
            );
        }
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
        let provider_ids = if let Some(feed) = self.feed.as_mut() {
            let mut values = Vec::new();
            for market in selected.values() {
                match feed.subscribe(market) {
                    Ok(provider_id) => values.push((market.market_id.clone(), provider_id)),
                    Err(error) => {
                        for (_, provider_id) in values {
                            let _ = feed.unsubscribe(&provider_id);
                        }
                        return Err(error);
                    }
                }
            }
            Some(values)
        } else {
            None
        };
        let subscription_id = id.clone();
        self.dynamic_intents.insert(
            id,
            DynamicIntent {
                owner_id,
                query,
                max_members: self.max_dynamic_members,
                members: selected.clone(),
            },
        );
        if let Some(provider_ids) = provider_ids {
            for (market_id, provider_id) in provider_ids {
                self.provider_subscriptions
                    .insert((subscription_id.clone(), market_id), provider_id);
            }
        }
        self.generation += 1;
        Ok(diff_members(&previous, &selected))
    }

    pub fn unsubscribe(&mut self, id: &SubscriptionId) -> bool {
        let provider_ids: Vec<_> = self
            .provider_subscriptions
            .iter()
            .filter(|((subscription_id, _), _)| subscription_id == id)
            .map(|(key, provider_id)| (key.clone(), provider_id.clone()))
            .collect();
        if let Some(feed) = self.feed.as_mut() {
            for (_, provider_id) in &provider_ids {
                let _ = feed.unsubscribe(provider_id);
            }
        }
        for (key, _) in provider_ids {
            self.provider_subscriptions.remove(&key);
        }
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
            let previous_ids: BTreeSet<_> = previous.keys().cloned().collect();
            let selected_ids: BTreeSet<_> = selected.keys().cloned().collect();
            let added_ids: Vec<_> = selected_ids.difference(&previous_ids).cloned().collect();
            let removed_ids: Vec<_> = previous_ids.difference(&selected_ids).cloned().collect();
            if let Some(feed) = self.feed.as_mut() {
                let mut added_provider_ids = Vec::new();
                for market_id in &added_ids {
                    let market = selected.get(market_id).expect("selected member exists");
                    let provider_id = match feed.subscribe(market) {
                        Ok(provider_id) => provider_id,
                        Err(error) => {
                            for provider_id in added_provider_ids {
                                let _ = feed.unsubscribe(&provider_id);
                            }
                            return Err(error);
                        }
                    };
                    added_provider_ids.push(provider_id.clone());
                    self.provider_subscriptions
                        .insert((id.clone(), market_id.clone()), provider_id);
                }
                for market_id in &removed_ids {
                    if let Some(provider_id) = self
                        .provider_subscriptions
                        .remove(&(id.clone(), market_id.clone()))
                    {
                        if let Err(error) = feed.unsubscribe(&provider_id) {
                            for added_market_id in &added_ids {
                                if let Some(added_provider_id) = self
                                    .provider_subscriptions
                                    .remove(&(id.clone(), added_market_id.clone()))
                                {
                                    let _ = feed.unsubscribe(&added_provider_id);
                                }
                            }
                            self.provider_subscriptions
                                .insert((id.clone(), market_id.clone()), provider_id);
                            return Err(error);
                        }
                    }
                }
            }
            intent.members = selected.clone();
            let diff = diff_members(&previous, &selected);
            if diff.added.len() + diff.removed.len() > 0 {
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
        self.latest.insert(market_id.clone(), observation);
        if self.feed_status == FeedStatus::WarmingUp {
            self.feed_status = FeedStatus::Ready;
        }
        self.event_sequence += 1;
        if let Some(publisher) = self.event_publisher.as_mut() {
            let value = self.latest.get(&market_id).expect("observation exists");
            publisher.publish(self.event_sequence, value)?;
        }
        Ok(self.event_sequence)
    }

    pub fn poll_feed(&mut self) -> Result<usize, String> {
        let observations = match self.feed.as_mut() {
            Some(feed) => match feed.poll() {
                Ok(observations) => observations,
                Err(error) => {
                    self.feed_status = FeedStatus::Degraded;
                    return Err(error);
                }
            },
            None => return Err("market feed is not configured".to_string()),
        };
        let orderbook_updates = match self.feed.as_mut() {
            Some(feed) => match feed.poll_orderbooks() {
                Ok(updates) => updates,
                Err(error) => {
                    self.feed_status = FeedStatus::Degraded;
                    return Err(error);
                }
            },
            None => Vec::new(),
        };
        let count = observations.len() + orderbook_updates.len();
        for observation in observations {
            self.apply_observation(observation)?;
        }
        for update in orderbook_updates {
            let result = if update.snapshot {
                OrderBook::snapshot(
                    update.market_id,
                    update.instrument_id,
                    update.last_sequence,
                    update.event_time_unix_nanos,
                    update.bids,
                    update.asks,
                )
                .and_then(|book| self.apply_orderbook_snapshot(book).map(|_| ()))
            } else {
                self.apply_orderbook_delta(OrderBookDelta {
                    market_id: update.market_id,
                    instrument_id: update.instrument_id,
                    first_sequence: update.first_sequence,
                    last_sequence: update.last_sequence,
                    event_time_unix_nanos: update.event_time_unix_nanos,
                    bids: update.bids,
                    asks: update.asks,
                })
                .map(|_| ())
            };
            if let Err(error) = result {
                self.feed_status = FeedStatus::Degraded;
                return Err(error);
            }
        }
        Ok(count)
    }

    pub fn recover_feed(&mut self) -> Result<(), String> {
        let feed = self
            .feed
            .as_mut()
            .ok_or_else(|| "market feed is not configured".to_string())?;
        self.feed_status = FeedStatus::Reconnecting;
        if let Err(error) = feed.recover() {
            self.feed_status = FeedStatus::Degraded;
            return Err(error);
        }
        self.feed_status = FeedStatus::WarmingUp;
        Ok(())
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
    ReconcileResult {
        added: current_ids.difference(&previous_ids).cloned().collect(),
        removed: previous_ids.difference(&current_ids).cloned().collect(),
        unchanged: current_ids.intersection(&previous_ids).cloned().collect(),
    }
}
