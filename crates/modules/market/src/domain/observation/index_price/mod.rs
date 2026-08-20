use kairos_primitives::decimal::{Price, Rate};
use kairos_primitives::market::SourceId;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
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
    pub source_id: SourceId,
}
