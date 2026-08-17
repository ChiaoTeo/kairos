//! Normalized market replay input for backtest and paper simulation.

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ObservationScope {
    Market {
        market_id: String,
    },
    Consolidated {
        instrument_id: String,
        network_id: Option<String>,
    },
}

impl ObservationScope {
    pub fn validate_for(&self, instrument_id: &str) -> Result<(), String> {
        match self {
            Self::Market { market_id } if !market_id.trim().is_empty() => Ok(()),
            Self::Consolidated {
                instrument_id: scoped,
                network_id,
            } if !scoped.trim().is_empty()
                && scoped == instrument_id
                && network_id
                    .as_ref()
                    .is_none_or(|value| !value.trim().is_empty()) =>
            {
                Ok(())
            }
            Self::Market { .. } => Err("market observation scope requires market_id".into()),
            Self::Consolidated { .. } => {
                Err("consolidated observation scope must match the observation instrument".into())
            }
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Quote {
    pub scope: ObservationScope,
    pub instrument_id: String,
    pub bid_price: Option<String>,
    pub bid_quantity: Option<String>,
    pub ask_price: Option<String>,
    pub ask_quantity: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Bar {
    pub scope: ObservationScope,
    pub instrument_id: String,
    pub timeframe: String,
    pub open: String,
    pub high: String,
    pub low: String,
    pub close: String,
    pub volume: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
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
