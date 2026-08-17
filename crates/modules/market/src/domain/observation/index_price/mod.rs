use kairos_primitives::{InstrumentId, Price, Rate, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IndexPrice {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub spot_index_price: Option<Price>,
    pub contract_index_price: Option<Price>,
    pub index_price: Option<Price>,
    pub funding_rate: Option<Rate>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}
