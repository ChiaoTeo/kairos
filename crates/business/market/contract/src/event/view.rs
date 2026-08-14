use kairos_protocol::generated::kairos::market::v_2 as fb;

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
