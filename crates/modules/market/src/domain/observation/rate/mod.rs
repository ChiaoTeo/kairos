use kairos_primitives::{InstrumentId, MarketId, Price, Rate as FixedRate, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Rate {
    pub rate_id: String,
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub basis: String,
    pub value: FixedRate,
    pub mark_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}
