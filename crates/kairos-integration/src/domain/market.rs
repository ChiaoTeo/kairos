//! Provider-neutral market data exchanged by market connections.

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum MarketEventKind {
    Snapshot,
    BookSnapshot,
    Quote,
    Trade,
    BookDelta,
    Heartbeat,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketEvent {
    pub symbol: String,
    pub kind: MarketEventKind,
    pub price: Option<String>,
    pub quantity: Option<String>,
    pub ask_price: Option<String>,
    pub ask_quantity: Option<String>,
    pub bids: Vec<(String, String)>,
    pub asks: Vec<(String, String)>,
    pub first_sequence: Option<u64>,
    pub last_sequence: Option<u64>,
    pub sequence: Option<u64>,
    pub observed_at_unix_nanos: u64,
}

impl MarketEvent {
    pub fn snapshot_key(&self) -> (&str, MarketEventKind, Option<&str>, Option<&str>) {
        (
            &self.symbol,
            self.kind,
            self.price.as_deref(),
            self.quantity.as_deref(),
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketQuote {
    pub symbol: String,
    pub bid_price: Option<String>,
    pub bid_quantity: Option<String>,
    pub ask_price: Option<String>,
    pub ask_quantity: Option<String>,
    pub last_price: Option<String>,
    pub observed_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketTrade {
    pub symbol: String,
    pub trade_id: Option<String>,
    pub price: String,
    pub quantity: String,
    pub is_buyer_maker: Option<bool>,
    pub event_at_unix_nanos: u64,
}
