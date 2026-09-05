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

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum MarketObservationError {
    #[error("consolidated observation scope must match the observation instrument")]
    ScopeInstrumentMismatch {
        scoped_instrument_id: InstrumentId,
        observation_instrument_id: InstrumentId,
    },
}

impl MarketObservationError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::ScopeInstrumentMismatch { .. } => {
                "execution.market_observation.scope_instrument_mismatch"
            },
        }
    }
}

impl ObservationScope {
    pub fn validate_for(&self, instrument_id: &InstrumentId) -> Result<(), MarketObservationError> {
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
            Self::Consolidated {
                instrument_id: scoped,
                ..
            } => Err(MarketObservationError::ScopeInstrumentMismatch {
                scoped_instrument_id: scoped.clone(),
                observation_instrument_id: instrument_id.clone(),
            }),
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn consolidated_scope_mismatch_is_structured() {
        let scoped = InstrumentId::new("ETH-USDT").unwrap();
        let observed = InstrumentId::new("BTC-USDT").unwrap();
        let error = ObservationScope::Consolidated {
            instrument_id: scoped.clone(),
            network_id: None,
        }
        .validate_for(&observed)
        .unwrap_err();

        assert_eq!(
            error,
            MarketObservationError::ScopeInstrumentMismatch {
                scoped_instrument_id: scoped,
                observation_instrument_id: observed,
            }
        );
        assert_eq!(
            error.code(),
            "execution.market_observation.scope_instrument_mismatch"
        );
    }
}
