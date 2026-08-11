use super::{observations::MarketObservation, orderbook::OrderBook};

/// Every mutation which advances Market's public event sequence must have a
/// corresponding event-plane representation. Keeping this enum beside the
/// domain state prevents snapshots and live consumers from observing
/// different sequence histories.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MarketEvent {
    Observation(MarketObservation),
    OrderBook(OrderBook),
}
