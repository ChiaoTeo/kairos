use super::protocol::MarketFeed;
use crate::domain::market::{MarketDescriptor, MarketSelectionQuery};
use crate::domain::observations::MarketObservation;
use crate::domain::reference::ReferenceChanged;
use crate::domain::subscriptions::SubscriptionId;
use crate::services::actor::{MarketActor, MarketSnapshot, ReconcileResult};

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

pub struct MarketApplication {
    actor: MarketActor,
}

impl MarketApplication {
    pub fn new(actor: MarketActor) -> Self {
        Self { actor }
    }

    pub fn subscribe_static(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: MarketDescriptor,
    ) -> Result<(), MarketError> {
        self.actor
            .subscribe_static(id, owner_id, market)
            .map_err(MarketError::Invalid)
    }

    pub fn subscribe_dynamic(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        markets: Vec<MarketDescriptor>,
    ) -> Result<ReconcileResult, MarketError> {
        self.actor
            .subscribe_dynamic(id, owner_id, query, markets)
            .map_err(MarketError::Invalid)
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
        self.actor.unsubscribe(id)
    }

    pub fn ingest(&mut self, observation: MarketObservation) -> Result<u64, MarketError> {
        self.actor
            .apply_observation(observation)
            .map_err(MarketError::Invalid)
    }

    pub fn snapshot(&self) -> MarketSnapshot {
        self.actor.snapshot()
    }

    pub fn attach_feed(&mut self, feed: Box<dyn MarketFeed>) {
        self.actor.attach_feed(feed);
    }

    pub fn drain_events(&mut self) -> Vec<(u64, MarketObservation)> {
        self.actor.drain_events()
    }

    pub fn poll_feed(&mut self) -> Result<usize, MarketError> {
        self.actor.poll_feed().map_err(MarketError::Invalid)
    }

    pub fn recover_feed(&mut self) -> Result<(), MarketError> {
        self.actor.recover_feed().map_err(MarketError::Invalid)
    }
}
