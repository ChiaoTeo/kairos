//! Provider-neutral market data exchanged by market connections.

use std::collections::BTreeSet;

use kairos_domain_types::{FillId, Price, Quantity, Rate, Sequence, Symbol, UnixNanos};

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum MarketDataKind {
    Quote,
    Trade,
    Bar,
    TradeBar,
    QuoteBar,
    OrderBook,
    Greeks,
    Ticker24h,
    MarkPrice,
    IndexPrice,
    FundingRate,
    OpenInterest,
    InstrumentStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MarketStreamCapabilities {
    pub realtime: BTreeSet<MarketDataKind>,
    pub historical: BTreeSet<MarketDataKind>,
    pub orderbook_resync: bool,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum MarketEventKind {
    Snapshot,
    BookSnapshot,
    Quote,
    Trade,
    Bar,
    TradeBar,
    QuoteBar,
    Greeks,
    Rate,
    Ticker24h,
    MarkPrice,
    IndexPrice,
    FundingRate,
    OpenInterest,
    InstrumentStatus,
    BookDelta,
    Heartbeat,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketBar {
    pub timeframe: String,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    pub volume: Option<Quantity>,
    pub derivation: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketGreeks {
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<Price>,
    pub delta: Option<Rate>,
    pub gamma: Option<Rate>,
    pub vega: Option<Rate>,
    pub theta: Option<Rate>,
    pub implied_volatility: Option<Rate>,
    pub derivation: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketEvent {
    pub symbol: Symbol,
    pub kind: MarketEventKind,
    pub price: Option<Price>,
    pub quantity: Option<Quantity>,
    pub rate: Option<Rate>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub bids: Vec<(Price, Quantity)>,
    pub asks: Vec<(Price, Quantity)>,
    pub bar: Option<MarketBar>,
    pub greeks: Option<MarketGreeks>,
    pub first_sequence: Option<Sequence>,
    pub last_sequence: Option<Sequence>,
    pub sequence: Option<Sequence>,
    pub observed_at_unix_nanos: UnixNanos,
}

impl MarketEvent {
    pub fn snapshot_key(&self) -> (&Symbol, MarketEventKind, Option<&Price>, Option<&Quantity>) {
        (
            &self.symbol,
            self.kind,
            self.price.as_ref(),
            self.quantity.as_ref(),
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketQuote {
    pub symbol: Symbol,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub last_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketTrade {
    pub symbol: Symbol,
    pub trade_id: Option<FillId>,
    pub price: Price,
    pub quantity: Quantity,
    pub is_buyer_maker: Option<bool>,
    pub event_at_unix_nanos: UnixNanos,
}
