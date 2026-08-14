use super::{
    freshness::MarketFreshness,
    observations::MarketObservation,
    orderbook::{OrderBook, OrderBookDelta},
};

/// Every mutation which advances Market's public event sequence must have a
/// corresponding event-plane representation. Keeping this enum beside the
/// domain state prevents snapshots and live consumers from observing
/// different sequence histories.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MarketEvent {
    Observation(MarketObservation),
    OrderBookSnapshot(OrderBook),
    OrderBookDelta(OrderBookDelta),
    OrderBookResyncRequired(OrderBookResyncRequired),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderBookResyncRequired {
    pub source_id: String,
    pub market_id: kairos_domain_types::MarketId,
    pub instrument_id: kairos_domain_types::InstrumentId,
    pub expected_sequence: kairos_domain_types::Sequence,
    pub observed_sequence: kairos_domain_types::Sequence,
    pub reason: String,
}

/// A mutation is the unit consumed by the process publication loop.  The
/// event plane and the view plane deliberately share the mutation sequence,
/// but a view update is scoped to exactly one resource.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketChange {
    pub sequence: kairos_domain_types::Sequence,
    pub event: Option<MarketEvent>,
    pub view: Option<MarketViewUpdate>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MarketViewUpdate {
    Observation(MarketObservation),
    OrderBook(OrderBook),
    Freshness(MarketFreshness),
}
