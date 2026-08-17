use std::collections::BTreeMap;

use crate::domain::freshness::DataFreshnessStatus;
use crate::domain::observation::MarketViewKey;
use crate::domain::observation::{MarketObservation, ObservationKind};
use crate::domain::source::SourceStatus;
use crate::domain::view::MarketView;
use kairos_primitives::{InstrumentId, MarketId, Money, Price, PriceDelta, Quantity, Rate};

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

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MarketDataAvailabilityQuery {
    pub market_id: Option<MarketId>,
    pub instrument_id: Option<InstrumentId>,
    pub observation_kind: Option<ObservationKind>,
    pub provider_id: Option<String>,
    pub configured_only: bool,
    pub ready_only: bool,
}

/// Query-time composition of canonical identity, adapter capability and
/// Market-owned runtime state. This is deliberately not a persisted entity.
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct MarketDataAvailability {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub source_id: String,
    pub provider_id: String,
    pub provider_product: String,
    pub provider_symbol: String,
    pub observation_capabilities: Vec<ObservationKind>,
    pub supported_by_adapter: bool,
    pub configured_in_workspace: bool,
    pub runtime_status: Option<SourceStatus>,
    pub freshness: BTreeMap<ObservationKind, DataFreshnessStatus>,
}

impl MarketQueryResult {
    pub(crate) fn from_view(view: MarketView) -> Self {
        Self { view }
    }
}
