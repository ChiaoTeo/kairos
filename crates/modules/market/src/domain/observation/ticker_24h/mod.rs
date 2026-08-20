use kairos_primitives::decimal::{Money, Price, Quantity, Rate};
use kairos_primitives::market::SourceId;
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Ticker24h {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub last_price: Option<Price>,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub open_price: Option<Price>,
    pub high_price: Option<Price>,
    pub low_price: Option<Price>,
    pub volume_base: Option<Quantity>,
    pub volume_quote: Option<Money>,
    pub price_change_abs: Option<Money>,
    pub price_change_pct: Option<Rate>,
    pub vwap: Option<Price>,
    pub mark_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: SourceId,
}
