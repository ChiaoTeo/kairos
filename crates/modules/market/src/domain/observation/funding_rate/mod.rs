use kairos_primitives::decimal::Rate;
use kairos_primitives::market::SourceId;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct FundingRate {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub funding_rate: Rate,
    pub funding_period_seconds: Option<u64>,
    pub next_funding_time_unix_nanos: Option<UnixNanos>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: SourceId,
}
