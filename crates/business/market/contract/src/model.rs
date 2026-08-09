//! Public Market snapshot models.

use std::collections::BTreeMap;

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
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
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Trade {
    pub market_id: String,
    pub instrument_id: String,
    pub trade_id: Option<String>,
    pub price: String,
    pub quantity: String,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
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
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
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

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketObservation {
    Quote(Quote),
    Trade(Trade),
    Bar(Bar),
    OptionGreeks(OptionGreeks),
}

impl MarketObservation {
    pub fn market_id(&self) -> &str {
        match self {
            Self::Quote(v) => &v.market_id,
            Self::Trade(v) => &v.market_id,
            Self::Bar(v) => &v.market_id,
            Self::OptionGreeks(v) => &v.market_id,
        }
    }
    pub fn view_key(&self) -> Result<MarketViewKey, String> {
        let qualifier = match self {
            Self::Bar(v) => v.timeframe.as_str(),
            _ => "",
        };
        MarketViewKey::with_qualifier(
            self.source_id(),
            self.market_id(),
            self.view_kind(),
            qualifier,
        )
    }
    pub fn view_kind(&self) -> &'static str {
        match self {
            Self::Quote(_) => "quote",
            Self::Trade(_) => "trade",
            Self::Bar(_) => "bar",
            Self::OptionGreeks(_) => "greek",
        }
    }
    pub fn source_id(&self) -> &str {
        match self {
            Self::Quote(v) => &v.source_id,
            Self::Trade(v) => &v.source_id,
            Self::Bar(v) => &v.source_id,
            Self::OptionGreeks(v) => &v.source_id,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PriceLevel {
    pub price: String,
    pub quantity: String,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct OrderBook {
    pub market_id: String,
    pub instrument_id: String,
    pub sequence: u64,
    pub event_time_unix_nanos: u64,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub synchronized: bool,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, serde::Serialize, serde::Deserialize)]
pub struct MarketDescriptor {
    pub market_id: String,
    pub instrument_id: String,
    pub venue_id: String,
    pub market_type: String,
    pub asset_type: Option<String>,
    pub underlying_instrument_id: Option<String>,
    pub source_symbol: String,
    pub status: String,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct SubscriptionState {
    pub id: String,
    pub owner_id: String,
    pub mode: String,
    pub members: BTreeMap<String, MarketDescriptor>,
}
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum FeedStatus {
    #[default]
    Disconnected,
    Ready,
    Reconnecting,
    WarmingUp,
    Degraded,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MarketSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub latest: BTreeMap<String, MarketObservation>,
    pub views: BTreeMap<String, MarketObservation>,
    pub order_books: BTreeMap<String, OrderBook>,
    pub subscriptions: Vec<SubscriptionState>,
    pub feed_status: FeedStatus,
}

#[derive(
    Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, serde::Serialize, serde::Deserialize,
)]
pub struct MarketViewKey {
    pub source_id: String,
    pub market_id: String,
    pub kind: String,
    pub qualifier: String,
}

impl MarketViewKey {
    pub fn new(source: &str, market: &str, kind: &str) -> Result<Self, String> {
        Ok(Self {
            source_id: source.into(),
            market_id: market.into(),
            kind: kind.into(),
            qualifier: String::new(),
        })
    }
    pub fn with_qualifier(
        source: &str,
        market: &str,
        kind: &str,
        qualifier: &str,
    ) -> Result<Self, String> {
        for value in [source, market, kind] {
            if value.contains('/') || value.contains('\\') || value == "." || value == ".." {
                return Err("market view identity contains an invalid path component".into());
            }
        }
        if qualifier.contains('/')
            || qualifier.contains('\\')
            || qualifier == "."
            || qualifier == ".."
        {
            return Err("market view identity contains an invalid path component".into());
        }
        Ok(Self {
            source_id: source.into(),
            market_id: market.into(),
            kind: kind.into(),
            qualifier: qualifier.into(),
        })
    }
    pub fn as_str(&self) -> String {
        let base = format!(
            "market.view.{}.{}.{}",
            self.source_id, self.market_id, self.kind
        );
        if self.qualifier.is_empty() {
            base
        } else {
            format!("{base}.{}", self.qualifier)
        }
    }
    pub fn path_parts(&self) -> Vec<&str> {
        let mut parts = vec![
            self.source_id.as_str(),
            self.market_id.as_str(),
            self.kind.as_str(),
        ];
        if !self.qualifier.is_empty() {
            parts.push(self.qualifier.as_str());
        }
        parts
    }
}
