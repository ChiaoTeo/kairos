use kairos_primitives::decimal::{Price, Rate};
use kairos_primitives::market::Provider;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OptionGreeks {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<Price>,
    pub delta: Option<Rate>,
    pub gamma: Option<Rate>,
    pub vega: Option<Rate>,
    pub theta: Option<Rate>,
    pub implied_volatility: Option<Rate>,
    pub observed_at_unix_nanos: UnixNanos,
    pub provider: Provider,
    pub derivation: String,
}
