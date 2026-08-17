use kairos_primitives::{InstrumentId, MarketId, Money, Price, Quantity, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Trade {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub trade_id: Option<String>,
    pub price: Price,
    pub quantity: Quantity,
    pub cost: Option<Money>,
    pub aggressor_side: Option<String>,
    pub observed_at_unix_nanos: UnixNanos,
    pub source_id: String,
}
