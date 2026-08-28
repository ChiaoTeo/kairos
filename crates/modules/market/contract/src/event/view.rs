use kairos_protocol::generated::kairos::market::v_2 as fb;
use kairos_protocol::{BorrowedEventView, BusinessEventKind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MarketEventKind {
    QuoteUpdated,
    TradeOccurred,
    BarCompleted,
    GreeksUpdated,
    RateUpdated,
    Ticker24hUpdated,
    MarkPriceUpdated,
    FundingRateUpdated,
    OpenInterestUpdated,
    IndexPriceUpdated,
    OrderBookSnapshotReceived,
    OrderBookDeltaReceived,
    OrderBookResyncRequired,
}

impl MarketEventKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::QuoteUpdated => "quote_updated",
            Self::TradeOccurred => "trade_occurred",
            Self::BarCompleted => "bar_completed",
            Self::GreeksUpdated => "greeks_updated",
            Self::RateUpdated => "rate_updated",
            Self::Ticker24hUpdated => "ticker_24h_updated",
            Self::MarkPriceUpdated => "mark_price_updated",
            Self::FundingRateUpdated => "funding_rate_updated",
            Self::OpenInterestUpdated => "open_interest_updated",
            Self::IndexPriceUpdated => "index_price_updated",
            Self::OrderBookSnapshotReceived => "order_book_snapshot_received",
            Self::OrderBookDeltaReceived => "order_book_delta_received",
            Self::OrderBookResyncRequired => "order_book_resync_required",
        }
    }
}

impl BusinessEventKind for MarketEventKind {
    fn as_str(self) -> &'static str {
        self.as_str()
    }
}

pub enum MarketEvent<'a> {
    QuoteUpdated(fb::QuoteUpdated<'a>),
    TradeOccurred(fb::TradeOccurred<'a>),
    BarCompleted(fb::BarCompleted<'a>),
    GreeksUpdated(fb::GreeksUpdated<'a>),
    RateUpdated(fb::RateUpdated<'a>),
    Ticker24hUpdated(fb::Ticker24hUpdated<'a>),
    MarkPriceUpdated(fb::MarkPriceUpdated<'a>),
    FundingRateUpdated(fb::FundingRateUpdated<'a>),
    OpenInterestUpdated(fb::OpenInterestUpdated<'a>),
    IndexPriceUpdated(fb::IndexPriceUpdated<'a>),
    OrderBookSnapshotReceived(fb::OrderBookSnapshotReceived<'a>),
    OrderBookDeltaReceived(fb::OrderBookDeltaReceived<'a>),
    OrderBookResyncRequired(fb::OrderBookResyncRequired<'a>),
}

pub type MarketEventView<'a> = MarketEvent<'a>;

impl<'a> MarketEvent<'a> {
    pub fn kind(&self) -> MarketEventKind {
        <Self as BorrowedEventView<'a>>::kind(self)
    }

    pub fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        <Self as BorrowedEventView<'a>>::metadata(self)
    }
}

impl<'a> BorrowedEventView<'a> for MarketEvent<'a> {
    type Kind = MarketEventKind;

    fn kind(&self) -> Self::Kind {
        match self {
            Self::QuoteUpdated(_) => MarketEventKind::QuoteUpdated,
            Self::TradeOccurred(_) => MarketEventKind::TradeOccurred,
            Self::BarCompleted(_) => MarketEventKind::BarCompleted,
            Self::GreeksUpdated(_) => MarketEventKind::GreeksUpdated,
            Self::RateUpdated(_) => MarketEventKind::RateUpdated,
            Self::Ticker24hUpdated(_) => MarketEventKind::Ticker24hUpdated,
            Self::MarkPriceUpdated(_) => MarketEventKind::MarkPriceUpdated,
            Self::FundingRateUpdated(_) => MarketEventKind::FundingRateUpdated,
            Self::OpenInterestUpdated(_) => MarketEventKind::OpenInterestUpdated,
            Self::IndexPriceUpdated(_) => MarketEventKind::IndexPriceUpdated,
            Self::OrderBookSnapshotReceived(_) => MarketEventKind::OrderBookSnapshotReceived,
            Self::OrderBookDeltaReceived(_) => MarketEventKind::OrderBookDeltaReceived,
            Self::OrderBookResyncRequired(_) => MarketEventKind::OrderBookResyncRequired,
        }
    }

    fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        match self {
            Self::QuoteUpdated(value) => value.metadata(),
            Self::TradeOccurred(value) => value.metadata(),
            Self::BarCompleted(value) => value.metadata(),
            Self::GreeksUpdated(value) => value.metadata(),
            Self::RateUpdated(value) => value.metadata(),
            Self::Ticker24hUpdated(value) => value.metadata(),
            Self::MarkPriceUpdated(value) => value.metadata(),
            Self::FundingRateUpdated(value) => value.metadata(),
            Self::OpenInterestUpdated(value) => value.metadata(),
            Self::IndexPriceUpdated(value) => value.metadata(),
            Self::OrderBookSnapshotReceived(value) => value.metadata(),
            Self::OrderBookDeltaReceived(value) => value.metadata(),
            Self::OrderBookResyncRequired(value) => value.metadata(),
        }
    }
}
