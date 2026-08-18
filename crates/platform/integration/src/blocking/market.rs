//! Synchronous market capabilities.

use kairos_primitives::ParticipantSymbol;

use crate::{
    HistoricalBarRequest, HistoricalWindow, IntegrationError, MarketBar, MarketBarRequest,
    MarketFundingRate, MarketGreeks, MarketIndexPrice, MarketMarkPrice, MarketOpenInterest,
    MarketOrderBook, MarketOrderBookRequest, MarketQuote, MarketStatus, MarketSubscription,
    MarketSubscriptionId, MarketSubscriptionOutcome, MarketSubscriptionRequest, MarketTicker,
    MarketTrade,
};

pub trait MarketQuoteQuery: Send {
    fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError>;
}

pub trait MarketTradeQuery: Send {
    fn fetch_trades(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketTrade>, IntegrationError>;
}

pub trait MarketBarQuery: Send {
    fn fetch_bars(
        &mut self,
        request: &MarketBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError>;
}

pub trait MarketOrderBookQuery: Send {
    fn fetch_order_books(
        &mut self,
        request: &MarketOrderBookRequest,
    ) -> Result<Vec<MarketOrderBook>, IntegrationError>;
}

pub trait MarketTickerQuery: Send {
    fn fetch_tickers(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketTicker>, IntegrationError>;
}

pub trait MarketMarkPriceQuery: Send {
    fn fetch_mark_prices(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketMarkPrice>, IntegrationError>;
}

pub trait MarketIndexPriceQuery: Send {
    fn fetch_index_prices(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketIndexPrice>, IntegrationError>;
}

pub trait MarketFundingRateQuery: Send {
    fn fetch_funding_rates(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketFundingRate>, IntegrationError>;
}

pub trait MarketOpenInterestQuery: Send {
    fn fetch_open_interest(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketOpenInterest>, IntegrationError>;
}

pub trait MarketGreeksQuery: Send {
    fn fetch_greeks(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketGreeks>, IntegrationError>;
}

pub trait MarketStatusQuery: Send {
    fn fetch_statuses(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketStatus>, IntegrationError>;
}

pub trait MarketSubscriptionCommand: Send {
    fn subscribe(
        &mut self,
        request: MarketSubscriptionRequest,
    ) -> Result<MarketSubscriptionOutcome<MarketSubscription>, IntegrationError>;
    fn unsubscribe(
        &mut self,
        subscription: MarketSubscriptionId,
    ) -> Result<MarketSubscriptionOutcome<()>, IntegrationError>;
}

pub trait HistoricalBarQuery: Send {
    fn fetch_bars(
        &mut self,
        request: &HistoricalBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError>;
}

pub trait HistoricalQuoteQuery: Send {
    fn fetch_quotes(
        &mut self,
        window: &HistoricalWindow,
    ) -> Result<Vec<MarketQuote>, IntegrationError>;
}

pub trait HistoricalTradeQuery: Send {
    fn fetch_trades(
        &mut self,
        window: &HistoricalWindow,
    ) -> Result<Vec<MarketTrade>, IntegrationError>;
}
