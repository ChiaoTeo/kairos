use kairos_primitives::{InstrumentId, MarketId, Price, Rate, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OptionGreeks {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<Price>,
    pub delta: Option<Rate>,
    pub gamma: Option<Rate>,
    pub vega: Option<Rate>,
    pub theta: Option<Rate>,
    pub implied_volatility: Option<Rate>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
    pub derivation: String,
}
