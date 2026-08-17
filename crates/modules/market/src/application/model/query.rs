use crate::domain::observation::MarketObservation;
use crate::domain::observation::MarketViewKey;
use crate::domain::view::MarketView;
use kairos_primitives::{Money, Price, PriceDelta, Quantity, Rate};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OrderBookSide {
    Buy,
    Sell,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionEstimate {
    pub requested_quantity: Quantity,
    pub filled_quantity: Quantity,
    pub notional: Money,
    pub vwap: Price,
    pub slippage_abs: PriceDelta,
    pub slippage_pct: Rate,
}

/// Stable read model for the latest value of one market view.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketObservationResult {
    pub key: MarketViewKey,
    pub observation: MarketObservation,
}

/// Stable read-side access to current Market state.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketQueryResult {
    pub(crate) view: MarketView,
}

impl MarketQueryResult {
    pub(crate) fn from_view(view: MarketView) -> Self {
        Self { view }
    }
}
