use kairos_primitives::{InstrumentId, Price, Rate, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarkPrice {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub mark_price: Price,
    pub index_price: Option<Price>,
    pub estimated_settlement_price: Option<Price>,
    pub funding_rate: Option<Rate>,
    pub next_funding_time_unix_nanos: Option<UnixNanos>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}
