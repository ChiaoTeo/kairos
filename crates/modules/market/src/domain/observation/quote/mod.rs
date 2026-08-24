use kairos_primitives::decimal::{Price, Quantity};
use kairos_primitives::market::Provider;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
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
    pub provider: Provider,
}
