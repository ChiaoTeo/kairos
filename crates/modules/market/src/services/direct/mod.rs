//! Short-lived provider connections used by standalone Market queries.

use kairos_conflux::{
    BinanceOptionsRestConnection, BinanceSpotRestConnection, BinanceStocksRestConnection,
    BinanceUsdMRestConnection, HistoricalBarQuery, HistoricalBarRequest, HistoricalQuoteQuery,
    HistoricalTradeQuery, HistoricalWindow, MarketBar, MarketBarQuery, MarketBarRequest,
    MarketGreeks, MarketGreeksQuery, MarketOrderBook, MarketOrderBookQuery, MarketOrderBookRequest,
    MarketQuote, MarketQuoteQuery, MarketTrade, MarketTradeQuery, MassiveRestConnection,
};
use kairos_primitives::decimal::{Price, Quantity, Rate};
use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::market::{ObservationKind, Provider};
use kairos_primitives::reference::InstrumentId;
use kairos_primitives::time::UnixNanos;

use crate::domain::observation::{Bar, MarketObservation, ObservationScope, Quote, Trade};

pub(crate) enum DirectMarketConnection {
    BinanceSpot(BinanceSpotRestConnection),
    BinanceUsdM(BinanceUsdMRestConnection),
    BinanceEquity(BinanceStocksRestConnection),
    BinanceOptions(BinanceOptionsRestConnection),
    MassiveEquity(MassiveRestConnection),
}

pub(crate) enum DirectHistoricalConnection {
    Binance(BinanceSpotRestConnection),
    Massive(MassiveRestConnection),
}

#[derive(Clone, Copy)]
pub(crate) enum DirectHistoricalKind {
    Bar,
    Quote,
    Trade,
}

pub(crate) enum DirectMarketSnapshot {
    Quote(DirectQuoteSnapshot),
    Trade(DirectTradeSnapshot),
    Bar(DirectBarSnapshot),
    OrderBook(DirectOrderBookSnapshot),
    Greeks(DirectGreeksSnapshot),
}

pub(crate) struct DirectQuoteSnapshot {
    pub(crate) bid_venue_code: Option<String>,
    pub(crate) ask_venue_code: Option<String>,
    pub(crate) tape: Option<u32>,
    pub(crate) symbol: String,
    pub(crate) bid_price: Option<Price>,
    pub(crate) bid_quantity: Option<Quantity>,
    pub(crate) ask_price: Option<Price>,
    pub(crate) ask_quantity: Option<Quantity>,
    pub(crate) last_price: Option<Price>,
    pub(crate) observed_at_unix_nanos: UnixNanos,
}

pub(crate) struct DirectTradeSnapshot {
    pub(crate) symbol: String,
    pub(crate) price: Price,
    pub(crate) quantity: Quantity,
    pub(crate) is_buyer_maker: Option<bool>,
    pub(crate) event_at_unix_nanos: UnixNanos,
}

pub(crate) struct DirectBarSnapshot {
    pub(crate) symbol: String,
    pub(crate) interval: String,
    pub(crate) open: Price,
    pub(crate) high: Price,
    pub(crate) low: Price,
    pub(crate) close: Price,
    pub(crate) volume: Option<Quantity>,
    pub(crate) opened_at_unix_nanos: UnixNanos,
    pub(crate) closed_at_unix_nanos: Option<UnixNanos>,
}

pub(crate) struct DirectOrderBookSnapshot {
    pub(crate) symbol: String,
    pub(crate) bids: Vec<(Price, Quantity)>,
    pub(crate) asks: Vec<(Price, Quantity)>,
}

pub(crate) struct DirectGreeksSnapshot {
    pub(crate) symbol: String,
    pub(crate) expiry_unix_nanos: Option<UnixNanos>,
    pub(crate) strike: Option<Price>,
    pub(crate) delta: Option<Rate>,
    pub(crate) gamma: Option<Rate>,
    pub(crate) vega: Option<Rate>,
    pub(crate) theta: Option<Rate>,
    pub(crate) implied_volatility: Option<Rate>,
}

impl From<MarketQuote> for DirectQuoteSnapshot {
    fn from(value: MarketQuote) -> Self {
        Self {
            bid_venue_code: value.venue.bid_exchange,
            ask_venue_code: value.venue.ask_exchange,
            tape: value.venue.tape,
            symbol: value.symbol.to_string(),
            bid_price: value.bid_price,
            bid_quantity: value.bid_quantity,
            ask_price: value.ask_price,
            ask_quantity: value.ask_quantity,
            last_price: value.last_price,
            observed_at_unix_nanos: value.observed_at_unix_nanos,
        }
    }
}

impl From<MarketTrade> for DirectTradeSnapshot {
    fn from(value: MarketTrade) -> Self {
        Self {
            symbol: value.symbol.to_string(),
            price: value.price,
            quantity: value.quantity,
            is_buyer_maker: value.is_buyer_maker,
            event_at_unix_nanos: value.event_at_unix_nanos,
        }
    }
}

impl From<MarketBar> for DirectBarSnapshot {
    fn from(value: MarketBar) -> Self {
        Self {
            symbol: value.symbol.to_string(),
            interval: value.interval,
            open: value.open,
            high: value.high,
            low: value.low,
            close: value.close,
            volume: value.volume,
            opened_at_unix_nanos: value.opened_at_unix_nanos,
            closed_at_unix_nanos: value.closed_at_unix_nanos,
        }
    }
}

impl From<MarketOrderBook> for DirectOrderBookSnapshot {
    fn from(value: MarketOrderBook) -> Self {
        Self {
            symbol: value.symbol.to_string(),
            bids: value.bids,
            asks: value.asks,
        }
    }
}

impl From<MarketGreeks> for DirectGreeksSnapshot {
    fn from(value: MarketGreeks) -> Self {
        Self {
            symbol: value.symbol.to_string(),
            expiry_unix_nanos: value.values.expiry_unix_nanos,
            strike: value.values.strike,
            delta: value.values.delta,
            gamma: value.values.gamma,
            vega: value.values.vega,
            theta: value.values.theta,
            implied_volatility: value.values.implied_volatility,
        }
    }
}

impl DirectMarketConnection {
    pub(crate) async fn snapshot(
        &mut self,
        symbol: &ParticipantSymbol,
        kind: ObservationKind,
        interval: &str,
        depth: u32,
    ) -> Result<DirectMarketSnapshot, Box<dyn std::error::Error>> {
        match self {
            Self::BinanceSpot(connection) => {
                fetch_standard(connection, symbol, kind, interval, depth).await
            },
            Self::BinanceUsdM(connection) => {
                fetch_standard(connection, symbol, kind, interval, depth).await
            },
            Self::BinanceEquity(connection) if kind == ObservationKind::Quote => {
                let value = connection
                    .fetch_quotes(std::slice::from_ref(symbol))
                    .await?
                    .into_iter()
                    .next()
                    .ok_or_else(|| format!("no quote data was returned for {symbol}"))?;
                Ok(DirectMarketSnapshot::Quote(value.into()))
            },
            Self::BinanceEquity(_) => {
                Err(format!("{} is unavailable from Binance Equity", kind.as_str()).into())
            },
            Self::BinanceOptions(connection) if kind == ObservationKind::OptionGreeks => {
                let value = connection
                    .fetch_greeks(std::slice::from_ref(symbol))
                    .await?
                    .into_iter()
                    .next()
                    .ok_or_else(|| format!("no Greeks data was returned for {symbol}"))?;
                Ok(DirectMarketSnapshot::Greeks(value.into()))
            },
            Self::BinanceOptions(connection) => {
                fetch_standard(connection, symbol, kind, interval, depth).await
            },
            Self::MassiveEquity(connection) => {
                fetch_massive(connection, symbol, kind, interval).await
            },
        }
    }
}

impl DirectHistoricalConnection {
    pub(crate) async fn fetch(
        &mut self,
        kind: DirectHistoricalKind,
        symbol: ParticipantSymbol,
        start_time_unix_nanos: UnixNanos,
        end_time_unix_nanos: UnixNanos,
        interval: String,
        adjusted: bool,
        scope: ObservationScope,
        instrument_id: InstrumentId,
        provider: Provider,
    ) -> Result<Vec<MarketObservation>, Box<dyn std::error::Error>> {
        let window = HistoricalWindow {
            symbol,
            start_time_unix_nanos,
            end_time_unix_nanos,
        };
        let bar_request = HistoricalBarRequest {
            window: window.clone(),
            interval,
            adjusted: Some(adjusted),
        };
        match self {
            Self::Binance(connection) => {
                historical_observations(
                    connection,
                    kind,
                    &window,
                    &bar_request,
                    scope,
                    instrument_id,
                    provider.clone(),
                    false,
                )
                .await
            },
            Self::Massive(connection) => {
                historical_observations(
                    connection,
                    kind,
                    &window,
                    &bar_request,
                    scope,
                    instrument_id,
                    provider,
                    true,
                )
                .await
            },
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn historical_observations<C>(
    connection: &mut C,
    kind: DirectHistoricalKind,
    window: &HistoricalWindow,
    bar_request: &HistoricalBarRequest,
    scope: ObservationScope,
    instrument_id: InstrumentId,
    provider: Provider,
    consolidated: bool,
) -> Result<Vec<MarketObservation>, Box<dyn std::error::Error>>
where
    C: HistoricalBarQuery + HistoricalQuoteQuery + HistoricalTradeQuery,
{
    match kind {
        DirectHistoricalKind::Bar => Ok(connection
            .fetch_bars(bar_request)
            .await?
            .into_iter()
            .map(|bar| {
                MarketObservation::Bar(Bar {
                    scope: scope.clone(),
                    instrument_id: instrument_id.clone(),
                    timeframe: bar.interval,
                    open: bar.open,
                    high: bar.high,
                    low: bar.low,
                    close: bar.close,
                    volume: bar.volume,
                    observed_at_unix_nanos: bar.opened_at_unix_nanos,
                    provider: provider.clone(),
                    derivation: bar.derivation,
                })
            })
            .collect()),
        DirectHistoricalKind::Quote => Ok(connection
            .fetch_quotes(window)
            .await?
            .into_iter()
            .map(|quote| {
                MarketObservation::Quote(Quote {
                    scope: scope.clone(),
                    instrument_id: instrument_id.clone(),
                    bid_price: quote.bid_price.or(quote.last_price),
                    bid_quantity: quote.bid_quantity,
                    ask_price: quote.ask_price,
                    ask_quantity: quote.ask_quantity,
                    bid_venue_id: None,
                    ask_venue_id: None,
                    bid_venue_code: quote.venue.bid_exchange,
                    ask_venue_code: quote.venue.ask_exchange,
                    tape: quote.venue.tape,
                    observed_at_unix_nanos: quote.observed_at_unix_nanos,
                    provider: provider.clone(),
                })
            })
            .collect()),
        DirectHistoricalKind::Trade if consolidated => Err(
            "Massive historical trades require an explicit venue-code to canonical-market join; observation quarantined"
                .into(),
        ),
        DirectHistoricalKind::Trade => Ok(connection
            .fetch_trades(window)
            .await?
            .into_iter()
            .map(|trade| {
                MarketObservation::Trade(Trade {
                    scope: scope.clone(),
                    instrument_id: instrument_id.clone(),
                    trade_id: trade.participant_trade_id,
                    price: trade.price,
                    quantity: trade.quantity,
                    cost: None,
                    aggressor_side: None,
                    venue_code: None,
                    tape: None,
                    trf_id: None,
                    participant_timestamp_unix_nanos: None,
                    trf_timestamp_unix_nanos: None,
                    observed_at_unix_nanos: trade.event_at_unix_nanos,
                    provider: provider.clone(),
                })
            })
            .collect()),
    }
}

async fn fetch_standard<C>(
    connection: &mut C,
    symbol: &ParticipantSymbol,
    kind: ObservationKind,
    interval: &str,
    depth: u32,
) -> Result<DirectMarketSnapshot, Box<dyn std::error::Error>>
where
    C: MarketQuoteQuery + MarketTradeQuery + MarketBarQuery + MarketOrderBookQuery,
{
    match kind {
        ObservationKind::Quote => Ok(DirectMarketSnapshot::Quote(
            MarketQuoteQuery::fetch_quotes(connection, std::slice::from_ref(symbol))
                .await?
                .into_iter()
                .next()
                .ok_or_else(|| format!("no quote data was returned for {symbol}"))?
                .into(),
        )),
        ObservationKind::Trade => Ok(DirectMarketSnapshot::Trade(
            MarketTradeQuery::fetch_trades(connection, std::slice::from_ref(symbol))
                .await?
                .into_iter()
                .last()
                .ok_or_else(|| format!("no trade data was returned for {symbol}"))?
                .into(),
        )),
        ObservationKind::Bar => Ok(DirectMarketSnapshot::Bar(
            connection
                .fetch_bars(&MarketBarRequest {
                    symbols: vec![symbol.clone()],
                    interval: interval.into(),
                    adjusted: None,
                })
                .await?
                .into_iter()
                .last()
                .ok_or_else(|| format!("no bar data was returned for {symbol}"))?
                .into(),
        )),
        ObservationKind::OrderBook => Ok(DirectMarketSnapshot::OrderBook(
            connection
                .fetch_order_books(&MarketOrderBookRequest {
                    symbols: vec![symbol.clone()],
                    depth: Some(depth),
                })
                .await?
                .into_iter()
                .next()
                .ok_or_else(|| format!("no order book data was returned for {symbol}"))?
                .into(),
        )),
        _ => Err(format!("{} is unavailable from this provider", kind.as_str()).into()),
    }
}

async fn fetch_massive(
    connection: &mut MassiveRestConnection,
    symbol: &ParticipantSymbol,
    kind: ObservationKind,
    interval: &str,
) -> Result<DirectMarketSnapshot, Box<dyn std::error::Error>> {
    match kind {
        ObservationKind::Quote => Ok(DirectMarketSnapshot::Quote(
            MarketQuoteQuery::fetch_quotes(connection, std::slice::from_ref(symbol))
                .await?
                .into_iter()
                .next()
                .ok_or_else(|| format!("no quote data was returned for {symbol}"))?
                .into(),
        )),
        ObservationKind::Trade => Ok(DirectMarketSnapshot::Trade(
            MarketTradeQuery::fetch_trades(connection, std::slice::from_ref(symbol))
                .await?
                .into_iter()
                .next()
                .ok_or_else(|| format!("no trade data was returned for {symbol}"))?
                .into(),
        )),
        ObservationKind::Bar => Ok(DirectMarketSnapshot::Bar(
            MarketBarQuery::fetch_bars(
                connection,
                &MarketBarRequest {
                    symbols: vec![symbol.clone()],
                    interval: interval.into(),
                    adjusted: Some(true),
                },
            )
            .await?
            .into_iter()
            .next()
            .ok_or_else(|| format!("no bar data was returned for {symbol}"))?
            .into(),
        )),
        _ => Err(format!("{} is unavailable from Massive", kind.as_str()).into()),
    }
}
