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
    pub cost: Option<String>,
    pub aggressor_side: Option<String>,
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
pub struct TradeBar {
    pub bar: Bar,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct QuoteBar {
    pub bar: Bar,
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
pub struct Rate {
    pub rate_id: String,
    pub market_id: String,
    pub instrument_id: String,
    pub basis: String,
    pub value: String,
    pub mark_price: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Ticker24h {
    pub market_id: String,
    pub instrument_id: String,
    pub last_price: Option<String>,
    pub bid_price: Option<String>,
    pub bid_quantity: Option<String>,
    pub ask_price: Option<String>,
    pub ask_quantity: Option<String>,
    pub open_price: Option<String>,
    pub high_price: Option<String>,
    pub low_price: Option<String>,
    pub volume_base: Option<String>,
    pub volume_quote: Option<String>,
    pub price_change_abs: Option<String>,
    pub price_change_pct: Option<String>,
    pub vwap: Option<String>,
    pub mark_price: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MarkPrice {
    pub market_id: String,
    pub instrument_id: String,
    pub mark_price: String,
    pub index_price: Option<String>,
    pub estimated_settlement_price: Option<String>,
    pub funding_rate: Option<String>,
    pub next_funding_time_unix_nanos: Option<u64>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct IndexPrice {
    pub market_id: String,
    pub instrument_id: String,
    pub spot_index_price: Option<String>,
    pub contract_index_price: Option<String>,
    pub index_price: Option<String>,
    pub funding_rate: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct FundingRate {
    pub market_id: String,
    pub instrument_id: String,
    pub funding_rate: String,
    pub funding_period_seconds: Option<u64>,
    pub next_funding_time_unix_nanos: Option<u64>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct OpenInterest {
    pub market_id: String,
    pub instrument_id: String,
    pub contracts: String,
    pub quote_value: Option<String>,
    pub change_24h: Option<String>,
    pub change_pct_24h: Option<String>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct InstrumentStatus {
    pub market_id: String,
    pub instrument_id: String,
    pub status: String,
    pub reason: Option<String>,
    pub effective_at_unix_nanos: Option<u64>,
    pub observed_at_unix_nanos: u64,
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarketObservation {
    Quote(Quote),
    Trade(Trade),
    Bar(Bar),
    TradeBar(TradeBar),
    QuoteBar(QuoteBar),
    OptionGreeks(OptionGreeks),
    Rate(Rate),
    Ticker24h(Ticker24h),
    MarkPrice(MarkPrice),
    IndexPrice(IndexPrice),
    FundingRate(FundingRate),
    OpenInterest(OpenInterest),
    InstrumentStatus(InstrumentStatus),
}

impl MarketObservation {
    pub fn market_id(&self) -> &str {
        match self {
            Self::Quote(v) => &v.market_id,
            Self::Trade(v) => &v.market_id,
            Self::Bar(v) => &v.market_id,
            Self::TradeBar(v) => &v.bar.market_id,
            Self::QuoteBar(v) => &v.bar.market_id,
            Self::OptionGreeks(v) => &v.market_id,
            Self::Rate(v) => &v.market_id,
            Self::Ticker24h(v) => &v.market_id,
            Self::MarkPrice(v) => &v.market_id,
            Self::IndexPrice(v) => &v.market_id,
            Self::FundingRate(v) => &v.market_id,
            Self::OpenInterest(v) => &v.market_id,
            Self::InstrumentStatus(v) => &v.market_id,
        }
    }
    pub fn view_key(&self) -> Result<MarketViewKey, String> {
        let qualifier = match self {
            Self::Bar(v) => v.timeframe.as_str(),
            Self::TradeBar(v) => v.bar.timeframe.as_str(),
            Self::QuoteBar(v) => v.bar.timeframe.as_str(),
            Self::Rate(v) => v.rate_id.as_str(),
            Self::Ticker24h(_)
            | Self::MarkPrice(_)
            | Self::IndexPrice(_)
            | Self::FundingRate(_)
            | Self::OpenInterest(_) => "",
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
            Self::TradeBar(_) => "trade_bar",
            Self::QuoteBar(_) => "quote_bar",
            Self::OptionGreeks(_) => "greek",
            Self::Rate(_) => "rate",
            Self::Ticker24h(_) => "ticker_24h",
            Self::MarkPrice(_) => "mark_price",
            Self::IndexPrice(_) => "index_price",
            Self::FundingRate(_) => "funding_rate",
            Self::OpenInterest(_) => "open_interest",
            Self::InstrumentStatus(_) => "instrument_status",
        }
    }
    pub fn source_id(&self) -> &str {
        match self {
            Self::Quote(v) => &v.source_id,
            Self::Trade(v) => &v.source_id,
            Self::Bar(v) => &v.source_id,
            Self::TradeBar(v) => &v.bar.source_id,
            Self::QuoteBar(v) => &v.bar.source_id,
            Self::OptionGreeks(v) => &v.source_id,
            Self::Rate(v) => &v.source_id,
            Self::Ticker24h(v) => &v.source_id,
            Self::MarkPrice(v) => &v.source_id,
            Self::IndexPrice(v) => &v.source_id,
            Self::FundingRate(v) => &v.source_id,
            Self::OpenInterest(v) => &v.source_id,
            Self::InstrumentStatus(v) => &v.source_id,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PriceLevel {
    pub price: String,
    pub quantity: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DepthPolicy {
    Full,
    TopN(u32),
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct DepthCursor {
    pub first_sequence: u64,
    pub last_sequence: u64,
    pub checksum: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct OrderBook {
    pub source_id: String,
    pub market_id: String,
    pub instrument_id: String,
    pub sequence: u64,
    pub event_time_unix_nanos: u64,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub synchronized: bool,
    pub depth_policy: DepthPolicy,
    pub cursor: DepthCursor,
    pub checksum: Option<String>,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, serde::Serialize, serde::Deserialize)]
pub struct MarketDescriptor {
    pub market_id: String,
    pub instrument_id: String,
    pub exchange_id: String,
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
    #[serde(default)]
    pub selectors: Vec<String>,
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

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum DataFreshnessStatus {
    #[default]
    Unknown,
    Current,
    Stale,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MarketFreshness {
    pub source_id: String,
    pub market_id: String,
    pub data_kind: String,
    pub last_event_time_unix_nanos: u64,
    pub last_received_time_unix_nanos: u64,
    pub event_sequence: u64,
    pub status: DataFreshnessStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MarketSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub latest: BTreeMap<String, MarketObservation>,
    pub views: BTreeMap<String, MarketObservation>,
    pub order_books: BTreeMap<String, OrderBook>,
    pub freshness: BTreeMap<String, MarketFreshness>,
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
