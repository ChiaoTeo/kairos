//! Short-lived provider connections used by standalone Market queries.

use kairos_conflux::{
    BinanceOptionsRestConnection, BinanceSpotRestConnection, BinanceStocksRestConnection,
    BinanceUsdMRestConnection, MarketBar, MarketBarQuery, MarketBarRequest, MarketGreeks,
    MarketGreeksQuery, MarketOrderBook, MarketOrderBookQuery, MarketOrderBookRequest, MarketQuote,
    MarketQuoteQuery, MarketTrade, MarketTradeQuery, MassiveRestConnection,
};
use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::market::ObservationKind;

pub(crate) enum DirectMarketConnection {
    BinanceSpot(BinanceSpotRestConnection),
    BinanceUsdM(BinanceUsdMRestConnection),
    BinanceEquity(BinanceStocksRestConnection),
    BinanceOptions(BinanceOptionsRestConnection),
    MassiveEquity(MassiveRestConnection),
}

pub(crate) enum DirectMarketSnapshot {
    Quote(MarketQuote),
    Trade(MarketTrade),
    Bar(MarketBar),
    OrderBook(MarketOrderBook),
    Greeks(MarketGreeks),
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
                Ok(DirectMarketSnapshot::Quote(value))
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
                Ok(DirectMarketSnapshot::Greeks(value))
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
            connection
                .fetch_quotes(std::slice::from_ref(symbol))
                .await?
                .into_iter()
                .next()
                .ok_or_else(|| format!("no quote data was returned for {symbol}"))?,
        )),
        ObservationKind::Trade => Ok(DirectMarketSnapshot::Trade(
            connection
                .fetch_trades(std::slice::from_ref(symbol))
                .await?
                .into_iter()
                .last()
                .ok_or_else(|| format!("no trade data was returned for {symbol}"))?,
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
                .ok_or_else(|| format!("no bar data was returned for {symbol}"))?,
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
                .ok_or_else(|| format!("no order book data was returned for {symbol}"))?,
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
            connection
                .fetch_quotes(std::slice::from_ref(symbol))
                .await?
                .into_iter()
                .next()
                .ok_or_else(|| format!("no quote data was returned for {symbol}"))?,
        )),
        ObservationKind::Trade => Ok(DirectMarketSnapshot::Trade(
            connection
                .fetch_trades(std::slice::from_ref(symbol))
                .await?
                .into_iter()
                .next()
                .ok_or_else(|| format!("no trade data was returned for {symbol}"))?,
        )),
        ObservationKind::Bar => Ok(DirectMarketSnapshot::Bar(
            connection
                .fetch_bars(&MarketBarRequest {
                    symbols: vec![symbol.clone()],
                    interval: interval.into(),
                    adjusted: Some(true),
                })
                .await?
                .into_iter()
                .next()
                .ok_or_else(|| format!("no bar data was returned for {symbol}"))?,
        )),
        _ => Err(format!("{} is unavailable from Massive", kind.as_str()).into()),
    }
}
