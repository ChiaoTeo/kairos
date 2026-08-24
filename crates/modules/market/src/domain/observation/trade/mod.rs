use kairos_primitives::decimal::{Money, Price, Quantity};
use kairos_primitives::market::Provider;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Trade {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub trade_id: Option<String>,
    pub price: Price,
    pub quantity: Quantity,
    pub cost: Option<Money>,
    pub aggressor_side: Option<String>,
    pub venue_code: Option<String>,
    pub tape: Option<u32>,
    pub trf_id: Option<u32>,
    pub participant_timestamp_unix_nanos: Option<UnixNanos>,
    pub trf_timestamp_unix_nanos: Option<UnixNanos>,
    pub observed_at_unix_nanos: UnixNanos,
    pub provider: Provider,
}
