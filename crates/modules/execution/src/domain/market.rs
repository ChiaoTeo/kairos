//! Normalized market replay facts for backtest and paper simulation.

use kairos_primitives::decimal::{Price, Quantity};
use kairos_primitives::market::Provider;
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ObservationScope {
    Market {
        market_id: MarketId,
    },
    Consolidated {
        instrument_id: InstrumentId,
        network_id: Option<String>,
    },
}

impl ObservationScope {
    pub fn validate_for(&self, instrument_id: &InstrumentId) -> Result<(), String> {
        match self {
            Self::Market { .. } => Ok(()),
            Self::Consolidated {
                instrument_id: scoped,
                network_id,
            } if scoped == instrument_id
                && network_id
                    .as_ref()
                    .is_none_or(|value| !value.trim().is_empty()) =>
            {
                Ok(())
            },
            Self::Consolidated { .. } => {
                Err("consolidated observation scope must match the observation instrument".into())
            },
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Quote {
    pub scope: ObservationScope,
    pub instrument_id: InstrumentId,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: Provider,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Bar {
    pub scope: ObservationScope,
    pub instrument_id: InstrumentId,
    pub timeframe: String,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    pub volume: Option<Quantity>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: Provider,
    pub derivation: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TradeBar {
    pub bar: Bar,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct QuoteBar {
    pub bar: Bar,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MarketObservation {
    Quote(Quote),
    Bar(Bar),
    TradeBar(TradeBar),
    QuoteBar(QuoteBar),
}
