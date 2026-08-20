use kairos_primitives::decimal::{Price, Quantity};
use kairos_primitives::market::SourceId;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Bar {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub timeframe: String,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    pub volume: Option<Quantity>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: SourceId,
    pub derivation: String,
}
