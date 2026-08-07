use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Quote {
    pub market_id: String,
    pub instrument_id: String,
    pub bid_price: Option<String>,
    pub bid_quantity: Option<String>,
    pub ask_price: Option<String>,
    pub ask_quantity: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Trade {
    pub market_id: String,
    pub instrument_id: String,
    pub trade_id: Option<String>,
    pub price: String,
    pub quantity: String,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MarketObservation {
    Quote(Quote),
    Trade(Trade),
}

impl MarketObservation {
    pub fn market_id(&self) -> &str {
        match self {
            Self::Quote(value) => &value.market_id,
            Self::Trade(value) => &value.market_id,
        }
    }

    pub fn observed_at_unix_nanos(&self) -> u64 {
        match self {
            Self::Quote(value) => value.observed_at_unix_nanos,
            Self::Trade(value) => value.observed_at_unix_nanos,
        }
    }
}
