use kairos_primitives::decimal::{Money, Quantity, Rate};
use kairos_primitives::market::SourceId;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OpenInterest {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub contracts: Quantity,
    pub quote_value: Option<Money>,
    pub change_24h: Option<Money>,
    pub change_pct_24h: Option<Rate>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: SourceId,
}
