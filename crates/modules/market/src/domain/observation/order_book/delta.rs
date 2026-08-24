use kairos_primitives::market::Provider;
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::time::{Sequence, UnixNanos};

use super::PriceLevel;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderBookDelta {
    pub provider: Provider,
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub first_sequence: Sequence,
    pub last_sequence: Sequence,
    pub event_time_unix_nanos: UnixNanos,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub checksum: Option<String>,
}
