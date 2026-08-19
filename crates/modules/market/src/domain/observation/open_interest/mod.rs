use kairos_primitives::{InstrumentId, Money, Quantity, Rate, SourceId, UnixNanos};
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
