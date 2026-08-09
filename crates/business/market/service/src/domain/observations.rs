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
pub struct Bar {
    pub market_id: String,
    pub instrument_id: String,
    pub timeframe: String,
    pub open: String,
    pub high: String,
    pub low: String,
    pub close: String,
    pub volume: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
    pub derivation: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OptionGreeks {
    pub market_id: String,
    pub instrument_id: String,
    pub expiry_unix_nanos: Option<u64>,
    pub strike: Option<String>,
    pub delta: Option<String>,
    pub gamma: Option<String>,
    pub vega: Option<String>,
    pub theta: Option<String>,
    pub implied_volatility: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
    pub derivation: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MarketObservation {
    Quote(Quote),
    Trade(Trade),
    Bar(Bar),
    OptionGreeks(OptionGreeks),
}

impl MarketObservation {
    pub fn market_id(&self) -> &str {
        match self {
            Self::Quote(value) => &value.market_id,
            Self::Trade(value) => &value.market_id,
            Self::Bar(value) => &value.market_id,
            Self::OptionGreeks(value) => &value.market_id,
        }
    }

    pub fn observed_at_unix_nanos(&self) -> u64 {
        match self {
            Self::Quote(value) => value.observed_at_unix_nanos,
            Self::Trade(value) => value.observed_at_unix_nanos,
            Self::Bar(value) => value.observed_at_unix_nanos,
            Self::OptionGreeks(value) => value.observed_at_unix_nanos,
        }
    }

    pub fn view_kind(&self) -> &'static str {
        match self {
            Self::Quote(_) => "quote",
            Self::Trade(_) => "trade",
            Self::Bar(_) => "bar",
            Self::OptionGreeks(_) => "greek",
        }
    }

    pub fn view_qualifier(&self) -> Option<&str> {
        match self {
            Self::Bar(value) => Some(&value.timeframe),
            _ => None,
        }
    }

    pub fn view_key(&self) -> Result<crate::MarketViewKey, String> {
        match self.view_qualifier() {
            Some(qualifier) => crate::MarketViewKey::with_qualifier(
                self.source_id(),
                self.market_id(),
                self.view_kind(),
                qualifier,
            ),
            None => crate::MarketViewKey::new(self.source_id(), self.market_id(), self.view_kind()),
        }
    }

    pub fn source_id(&self) -> &str {
        match self {
            Self::Quote(value) => &value.source_id,
            Self::Trade(value) => &value.source_id,
            Self::Bar(value) => &value.source_id,
            Self::OptionGreeks(value) => &value.source_id,
        }
    }
}
