use kairos_primitives::{InstrumentId, MarketId, Price, Quantity, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Bar {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub timeframe: String,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    pub volume: Option<Quantity>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
    pub derivation: String,
}
