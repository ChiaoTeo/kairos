use kairos_primitives::decimal::{Price, Rate as FixedRate};
use kairos_primitives::market::SourceId;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Rate {
    pub rate_id: String,
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub basis: String,
    pub value: FixedRate,
    pub mark_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: SourceId,
}
