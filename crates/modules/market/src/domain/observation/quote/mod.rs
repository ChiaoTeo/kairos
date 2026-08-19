use kairos_primitives::{InstrumentId, Price, Quantity, SourceId, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Quote {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub bid_venue_code: Option<String>,
    pub ask_venue_code: Option<String>,
    pub tape: Option<u32>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: SourceId,
}
