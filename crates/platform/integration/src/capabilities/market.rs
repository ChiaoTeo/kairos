//! Async market queries, subscription commands, and streams.

use std::future::Future;
use std::task::{Context, Poll};

use kairos_primitives::integration::ParticipantSymbol;

use crate::IntegrationError;
use crate::domain::market::{
    HistoricalBarRequest, HistoricalWindow, MarketBar, MarketBarRequest, MarketEvent,
    MarketFundingRate, MarketGreeks, MarketIndexPrice, MarketMarkPrice, MarketOpenInterest,
    MarketOrderBook, MarketOrderBookRequest, MarketQuote, MarketStatus, MarketSubscription,
    MarketSubscriptionId, MarketSubscriptionOutcome, MarketSubscriptionRequest, MarketTicker,
    MarketTrade,
};

pub trait MarketQuoteQuery: Send {
    fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> impl Future<Output = Result<Vec<MarketQuote>, IntegrationError>> + Send;
}

pub trait MarketTradeQuery: Send {
    fn fetch_trades(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> impl Future<Output = Result<Vec<MarketTrade>, IntegrationError>> + Send;
}

pub trait MarketBarQuery: Send {
    fn fetch_bars(
        &mut self,
        request: &MarketBarRequest,
    ) -> impl Future<Output = Result<Vec<MarketBar>, IntegrationError>> + Send;
}

pub trait MarketOrderBookQuery: Send {
    fn fetch_order_books(
        &mut self,
        request: &MarketOrderBookRequest,
    ) -> impl Future<Output = Result<Vec<MarketOrderBook>, IntegrationError>> + Send;
}

pub trait MarketTickerQuery: Send {
    fn fetch_tickers(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> impl Future<Output = Result<Vec<MarketTicker>, IntegrationError>> + Send;
}

pub trait MarketMarkPriceQuery: Send {
    fn fetch_mark_prices(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> impl Future<Output = Result<Vec<MarketMarkPrice>, IntegrationError>> + Send;
}

pub trait MarketIndexPriceQuery: Send {
    fn fetch_index_prices(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> impl Future<Output = Result<Vec<MarketIndexPrice>, IntegrationError>> + Send;
}

pub trait MarketFundingRateQuery: Send {
    fn fetch_funding_rates(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> impl Future<Output = Result<Vec<MarketFundingRate>, IntegrationError>> + Send;
}

pub trait MarketOpenInterestQuery: Send {
    fn fetch_open_interest(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> impl Future<Output = Result<Vec<MarketOpenInterest>, IntegrationError>> + Send;
}

pub trait MarketGreeksQuery: Send {
    fn fetch_greeks(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> impl Future<Output = Result<Vec<MarketGreeks>, IntegrationError>> + Send;
}

pub trait MarketStatusQuery: Send {
    fn fetch_statuses(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> impl Future<Output = Result<Vec<MarketStatus>, IntegrationError>> + Send;
}

pub trait MarketSubscriptionCommand: Send {
    fn subscribe(
        &mut self,
        request: MarketSubscriptionRequest,
    ) -> impl Future<
        Output = Result<MarketSubscriptionOutcome<MarketSubscription>, IntegrationError>,
    > + Send;
    fn unsubscribe(
        &mut self,
        subscription: MarketSubscriptionId,
    ) -> impl Future<Output = Result<MarketSubscriptionOutcome<()>, IntegrationError>> + Send;
}

pub trait MarketDataStream: Send {
    fn poll_next(&mut self, cx: &mut Context<'_>) -> Poll<Result<MarketEvent, IntegrationError>>;
}

pub trait HistoricalBarQuery: Send {
    fn fetch_bars(
        &mut self,
        request: &HistoricalBarRequest,
    ) -> impl Future<Output = Result<Vec<MarketBar>, IntegrationError>> + Send;
}

pub trait HistoricalQuoteQuery: Send {
    fn fetch_quotes(
        &mut self,
        window: &HistoricalWindow,
    ) -> impl Future<Output = Result<Vec<MarketQuote>, IntegrationError>> + Send;
}

pub trait HistoricalTradeQuery: Send {
    fn fetch_trades(
        &mut self,
        window: &HistoricalWindow,
    ) -> impl Future<Output = Result<Vec<MarketTrade>, IntegrationError>> + Send;
}
