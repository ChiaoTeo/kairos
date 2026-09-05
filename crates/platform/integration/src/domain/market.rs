//! Participant-neutral market data exchanged by market connections.

use std::collections::BTreeSet;

use kairos_primitives::decimal::{Price, Quantity, Rate};
use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::time::{Sequence, UnixNanos};

use super::ParticipantRejection;

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
pub struct Bar {
    pub timeframe: String,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    pub volume: Option<Quantity>,
    pub derivation: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Greeks {
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<Price>,
    pub delta: Option<Rate>,
    pub gamma: Option<Rate>,
    pub vega: Option<Rate>,
    pub theta: Option<Rate>,
    pub implied_volatility: Option<Rate>,
    pub derivation: String,
}

/// Participant-native venue evidence carried across the Integration boundary.
///
/// These values are deliberately not canonical `MarketId`s. Market
/// composition resolves them against Reference venue identity, while unknown
/// codes remain explicit evidence instead of falling back to a listing venue.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MarketVenueEvidence {
    pub trade_exchange: Option<String>,
    pub bid_exchange: Option<String>,
    pub ask_exchange: Option<String>,
    pub tape: Option<u32>,
    pub trf_id: Option<u32>,
    pub participant_timestamp_unix_nanos: Option<UnixNanos>,
    pub trf_timestamp_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketEvent {
    pub symbol: ParticipantSymbol,
    pub kind: MarketEventKind,
    pub price: Option<Price>,
    pub quantity: Option<Quantity>,
    pub rate: Option<Rate>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub bids: Vec<(Price, Quantity)>,
    pub asks: Vec<(Price, Quantity)>,
    pub bar: Option<Bar>,
    pub greeks: Option<Greeks>,
    pub first_sequence: Option<Sequence>,
    pub last_sequence: Option<Sequence>,
    pub sequence: Option<Sequence>,
    pub observed_at_unix_nanos: UnixNanos,
    pub venue: MarketVenueEvidence,
}

impl MarketEvent {
    pub fn snapshot_key(
        &self,
    ) -> (
        &ParticipantSymbol,
        MarketEventKind,
        Option<&Price>,
        Option<&Quantity>,
    ) {
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
    /// Provider-native evidence only; Reference owns canonical venue resolution.
    pub venue: MarketVenueEvidence,
    pub symbol: ParticipantSymbol,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    pub last_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketTrade {
    pub symbol: ParticipantSymbol,
    pub participant_trade_id: Option<String>,
    pub price: Price,
    pub quantity: Quantity,
    pub is_buyer_maker: Option<bool>,
    pub event_at_unix_nanos: UnixNanos,
}

/// One bounded historical bar observation returned by a query.
///
/// Live stream bars remain embedded in [`MarketEvent`]; bounded queries use
/// this self-contained fact so symbol and time-window identity are not carried
/// by a generic event envelope.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketBar {
    pub symbol: ParticipantSymbol,
    pub interval: String,
    pub open: Price,
    pub high: Price,
    pub low: Price,
    pub close: Price,
    pub volume: Option<Quantity>,
    pub opened_at_unix_nanos: UnixNanos,
    pub closed_at_unix_nanos: Option<UnixNanos>,
    pub adjusted: Option<bool>,
    pub derivation: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketOrderBook {
    pub symbol: ParticipantSymbol,
    pub bids: Vec<(Price, Quantity)>,
    pub asks: Vec<(Price, Quantity)>,
    pub sequence: Option<Sequence>,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketTicker {
    pub symbol: ParticipantSymbol,
    pub bid_price: Option<Price>,
    pub ask_price: Option<Price>,
    pub last_price: Option<Price>,
    pub open_price: Option<Price>,
    pub high_price: Option<Price>,
    pub low_price: Option<Price>,
    pub volume: Option<Quantity>,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketMarkPrice {
    pub symbol: ParticipantSymbol,
    pub price: Price,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketIndexPrice {
    pub symbol: ParticipantSymbol,
    pub price: Price,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketFundingRate {
    pub symbol: ParticipantSymbol,
    pub rate: Rate,
    pub next_funding_at_unix_nanos: Option<UnixNanos>,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketOpenInterest {
    pub symbol: ParticipantSymbol,
    pub quantity: Quantity,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketGreeks {
    pub symbol: ParticipantSymbol,
    pub values: Greeks,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketStatus {
    pub symbol: ParticipantSymbol,
    pub status: String,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketBarRequest {
    pub symbols: Vec<ParticipantSymbol>,
    pub interval: String,
    pub adjusted: Option<bool>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketOrderBookRequest {
    pub symbols: Vec<ParticipantSymbol>,
    pub depth: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketFeed {
    pub kind: MarketDataKind,
    pub symbol: Option<ParticipantSymbol>,
    pub interval: Option<String>,
    pub depth: Option<u32>,
    pub update_speed_millis: Option<u32>,
}

impl MarketFeed {
    pub fn quote(symbol: impl Into<String>) -> Result<Self, String> {
        Ok(Self {
            kind: MarketDataKind::Quote,
            symbol: Some(ParticipantSymbol::new(symbol.into()).map_err(|error| error.to_string())?),
            interval: None,
            depth: None,
            update_speed_millis: None,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketSubscriptionRequest {
    pub feeds: Vec<MarketFeed>,
}

impl MarketSubscriptionRequest {
    pub fn new(feeds: Vec<MarketFeed>) -> Result<Self, String> {
        if feeds.is_empty() {
            return Err("market subscription requires at least one feed".into());
        }
        Ok(Self { feeds })
    }

    pub fn quotes<I, S>(symbols: I) -> Result<Self, String>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Self::new(
            symbols
                .into_iter()
                .map(|symbol| MarketFeed::quote(symbol.into()))
                .collect::<Result<Vec<_>, _>>()?,
        )
    }

    pub fn symbols(&self) -> Result<Vec<String>, String> {
        self.feeds
            .iter()
            .map(|feed| {
                feed.symbol
                    .as_ref()
                    .map(|symbol| symbol.as_str().to_ascii_uppercase())
                    .ok_or_else(|| format!("{:?} feed requires a participant symbol", feed.kind))
            })
            .collect()
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct MarketSubscriptionId(pub u64);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MarketDelivery {
    Push,
    Polling,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketSubscription {
    pub id: MarketSubscriptionId,
    pub feeds: Vec<MarketFeed>,
    pub delivery: MarketDelivery,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MarketSubscriptionOutcome<T> {
    Confirmed(T),
    Rejected(ParticipantRejection),
    Indeterminate {
        provisional: Option<T>,
        reason: String,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HistoricalWindow {
    pub symbol: ParticipantSymbol,
    pub start_time_unix_nanos: UnixNanos,
    pub end_time_unix_nanos: UnixNanos,
}

impl HistoricalWindow {
    pub fn validate(&self) -> Result<(), String> {
        if self.end_time_unix_nanos < self.start_time_unix_nanos {
            return Err("historical market time window is invalid".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HistoricalBarRequest {
    pub window: HistoricalWindow,
    pub interval: String,
    pub adjusted: Option<bool>,
}

impl HistoricalBarRequest {
    pub fn validate(&self) -> Result<(), String> {
        self.window.validate()?;
        if self.interval.trim().is_empty() {
            return Err("historical bars require an interval".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ParticipantSymbol;
    use kairos_primitives::time::UnixNanos;

    use super::{HistoricalBarRequest, HistoricalWindow, MarketSubscriptionRequest};

    #[test]
    fn subscription_requires_a_feed() {
        assert!(MarketSubscriptionRequest::new(Vec::new()).is_err());
    }

    #[test]
    fn historical_window_must_be_ordered() {
        let request = HistoricalBarRequest {
            window: HistoricalWindow {
                symbol: ParticipantSymbol::new("BTCUSDT").unwrap(),
                start_time_unix_nanos: UnixNanos::new(2),
                end_time_unix_nanos: UnixNanos::new(1),
            },
            interval: "1m".into(),
            adjusted: None,
        };
        assert!(request.validate().is_err());
    }
}
