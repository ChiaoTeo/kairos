use crate::domain::market::{MarketDescriptor, MarketSelectionQuery};
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::{OrderBook, OrderBookDelta};
use crate::domain::reference::ReferenceChanged;
use crate::domain::snapshot::{MarketSnapshot, ReconcileResult};
use crate::domain::subscriptions::SubscriptionId;
use crate::services::actor::MarketActor;
use tracing::{debug, info, warn};

use super::query::MarketQueryResult;

#[derive(Debug, PartialEq, Eq)]
pub enum MarketError {
    Invalid(String),
    NotFound(String),
}

impl std::fmt::Display for MarketError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(value) => write!(formatter, "invalid market request: {value}"),
            Self::NotFound(value) => write!(formatter, "market not found: {value}"),
        }
    }
}

impl std::error::Error for MarketError {}

/// Public Market business facade.
///
/// Provider connection lifecycle belongs to `MarketRuntime`; this type only
/// owns business subscription state and normalized market state.
pub struct MarketApplication {
    pub(crate) actor: MarketActor,
}

impl MarketApplication {
    pub fn new(
        actor_id: impl Into<String>,
        max_dynamic_members: usize,
    ) -> Result<Self, MarketError> {
        let actor =
            MarketActor::new(actor_id, max_dynamic_members).map_err(MarketError::Invalid)?;
        Ok(Self { actor })
    }

    pub fn subscribe_static(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: MarketDescriptor,
    ) -> Result<(), MarketError> {
        self.subscribe_static_with_selectors(id, owner_id, market, Vec::new())
    }

    pub fn subscribe_static_with_selectors(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: MarketDescriptor,
        selectors: Vec<String>,
    ) -> Result<(), MarketError> {
        let subscription_id = id.0.clone();
        info!(event = "market_subscription_started", component = "market", subscription_id = %subscription_id, "static market subscription started");
        let result = if selectors.is_empty() {
            self.actor
                .subscribe_static(id, owner_id, market)
                .map_err(MarketError::Invalid)
        } else {
            self.actor
                .subscribe_static_with_selectors(id, owner_id, market, selectors)
                .map_err(MarketError::Invalid)
        };
        match &result {
            Ok(()) => {
                info!(event = "market_subscription_accepted", component = "market", subscription_id = %subscription_id, "static market subscription accepted")
            }
            Err(error) => {
                warn!(event = "market_subscription_rejected", component = "market", subscription_id = %subscription_id, error = %error, "static market subscription rejected")
            }
        }
        result
    }

    pub fn subscribe_dynamic(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        markets: Vec<MarketDescriptor>,
    ) -> Result<ReconcileResult, MarketError> {
        self.subscribe_dynamic_with_selectors(id, owner_id, query, markets, Vec::new())
    }

    pub fn subscribe_dynamic_with_selectors(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        markets: Vec<MarketDescriptor>,
        selectors: Vec<String>,
    ) -> Result<ReconcileResult, MarketError> {
        let subscription_id = id.0.clone();
        let market_count = markets.len();
        info!(event = "market_dynamic_subscription_started", component = "market", subscription_id = %subscription_id, market_count, "dynamic market subscription started");
        let result = if selectors.is_empty() {
            self.actor
                .subscribe_dynamic(id, owner_id, query, markets)
                .map_err(MarketError::Invalid)
        } else {
            self.actor
                .subscribe_dynamic_with_selectors(id, owner_id, query, markets, selectors)
                .map_err(MarketError::Invalid)
        };
        match &result {
            Ok(reconcile) => {
                info!(event = "market_dynamic_subscription_accepted", component = "market", subscription_id = %subscription_id, added = reconcile.added.len(), removed = reconcile.removed.len(), "dynamic market subscription accepted")
            }
            Err(error) => {
                warn!(event = "market_dynamic_subscription_rejected", component = "market", subscription_id = %subscription_id, error = %error, "dynamic market subscription rejected")
            }
        }
        result
    }

    pub fn reconcile_reference(
        &mut self,
        markets: Vec<MarketDescriptor>,
    ) -> Result<std::collections::BTreeMap<SubscriptionId, ReconcileResult>, MarketError> {
        self.actor
            .reconcile_reference(markets)
            .map_err(MarketError::Invalid)
    }

    pub fn apply_reference_change(
        &mut self,
        change: ReferenceChanged,
    ) -> Result<std::collections::BTreeMap<SubscriptionId, ReconcileResult>, MarketError> {
        self.actor
            .apply_reference_change(change)
            .map_err(MarketError::Invalid)
    }

    pub fn unsubscribe(&mut self, id: &SubscriptionId) -> bool {
        let removed = self.actor.unsubscribe(id);
        info!(event = "market_subscription_removed", component = "market", subscription_id = %id.0, removed, "market subscription removal processed");
        removed
    }

    pub fn ingest(&mut self, observation: MarketObservation) -> Result<u64, MarketError> {
        let result = self
            .actor
            .apply_observation(observation)
            .map_err(MarketError::Invalid);
        if let Err(error) = &result {
            warn!(event = "market_observation_rejected", component = "market", error = %error, "market observation rejected");
        } else {
            debug!(
                event = "market_observation_applied",
                component = "market",
                "market observation applied"
            );
        }
        result
    }

    pub fn ingest_orderbook_snapshot(&mut self, book: OrderBook) -> Result<u64, MarketError> {
        self.actor
            .apply_orderbook_snapshot(book)
            .map_err(MarketError::Invalid)
    }

    pub fn ingest_orderbook_delta(&mut self, delta: OrderBookDelta) -> Result<u64, MarketError> {
        self.actor
            .apply_orderbook_delta(delta)
            .map_err(MarketError::Invalid)
    }

    pub fn snapshot(&self) -> MarketSnapshot {
        self.actor.snapshot()
    }

    pub fn query(&self) -> MarketQueryResult {
        MarketQueryResult::new(self.actor.snapshot())
    }

    pub fn drain_events(&mut self) -> Vec<(u64, MarketObservation)> {
        self.actor
            .drain_events()
            .into_iter()
            .map(|(sequence, event)| (sequence.get(), event))
            .collect()
    }

    pub fn drain_events_limited(&mut self, limit: usize) -> Vec<(u64, MarketObservation)> {
        self.actor
            .drain_events_limited(limit)
            .into_iter()
            .map(|(sequence, event)| (sequence.get(), event))
            .collect()
    }
}
