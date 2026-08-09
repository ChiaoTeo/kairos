//! Read-side queries for the Market application.

use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::OrderBook;
use crate::domain::snapshot::{MarketSnapshot, SubscriptionState};
use crate::domain::view::MarketViewKey;

/// Stable read model for the latest value of one market view.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketObservationResult {
    pub key: MarketViewKey,
    pub observation: MarketObservation,
}

/// Stable read-side access to current Market state.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketQueryResult {
    pub snapshot: MarketSnapshot,
}

impl MarketQueryResult {
    pub(crate) fn new(snapshot: MarketSnapshot) -> Self {
        Self { snapshot }
    }

    pub fn latest(&self, market_id: &str) -> Option<&MarketObservation> {
        self.snapshot.latest.get(market_id)
    }

    pub fn view(&self, key: &MarketViewKey) -> Option<MarketObservationResult> {
        self.snapshot
            .views
            .get(&key.as_str())
            .cloned()
            .map(|observation| MarketObservationResult {
                key: key.clone(),
                observation,
            })
    }

    pub fn order_book(&self, market_id: &str) -> Option<&OrderBook> {
        self.snapshot.order_books.get(market_id)
    }

    pub fn subscriptions(&self) -> &[SubscriptionState] {
        &self.snapshot.subscriptions
    }
}
