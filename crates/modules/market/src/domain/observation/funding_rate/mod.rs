use kairos_primitives::{InstrumentId, MarketId, Rate, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingRate {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub funding_rate: Rate,
    pub funding_period_seconds: Option<u64>,
    pub next_funding_time_unix_nanos: Option<UnixNanos>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}
