use kairos_primitives::ParticipantSymbol;
use serde_json::Value;

use crate::services::participants::binance::market;
use crate::{
    ExternalInstrumentCatalog, ExternalInstrumentCatalogPage, InstrumentCatalogQuery,
    IntegrationError, MarketBar, MarketBarQuery, MarketBarRequest, MarketOrderBook,
    MarketOrderBookQuery, MarketOrderBookRequest, MarketQuote, MarketQuoteQuery, MarketTrade,
    MarketTradeQuery, ParticipantKind, ParticipantRef,
};

rest_connection!(BinanceAlphaTradingRestConnection, "advanced.alpha.rest");

impl InstrumentCatalogQuery for BinanceAlphaTradingRestConnection {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let value = self
            .service
            .public_get("/bapi/defi/v1/public/alpha-trade/get-exchange-info", &[])
            .await?;
        Ok(ExternalInstrumentCatalog {
            participant: participant(),
            instruments: market::spot_instruments(data(&value))?,
        })
    }

    async fn fetch_instruments_page(
        &mut self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<ExternalInstrumentCatalogPage, IntegrationError> {
        if cursor.is_some() {
            return Ok(ExternalInstrumentCatalogPage {
                catalog: ExternalInstrumentCatalog {
                    participant: participant(),
                    instruments: Vec::new(),
                },
                next_cursor: None,
                complete: true,
            });
        }
        let mut catalog = self.fetch_instruments().await?;
        if limit > 0 {
            catalog
                .instruments
                .truncate(limit.min(catalog.instruments.len()));
        }
        Ok(ExternalInstrumentCatalogPage {
            catalog,
            next_cursor: None,
            complete: true,
        })
    }
}

impl MarketQuoteQuery for BinanceAlphaTradingRestConnection {
    async fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        let mut quotes = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let value = self
                .service
                .public_get(
                    "/bapi/defi/v1/public/alpha-trade/ticker",
                    &[("symbol", symbol.as_str().into())],
                )
                .await?;
            quotes.push(market::quote(symbol, first(data(&value))?)?);
        }
        Ok(quotes)
    }
}

impl MarketTradeQuery for BinanceAlphaTradingRestConnection {
    async fn fetch_trades(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketTrade>, IntegrationError> {
        let mut trades = Vec::new();
        for symbol in symbols {
            let value = self
                .service
                .public_get(
                    "/bapi/defi/v1/public/alpha-trade/agg-trades",
                    &[("symbol", symbol.as_str().into()), ("limit", "100".into())],
                )
                .await?;
            trades.extend(market::trades(symbol, data(&value))?);
        }
        Ok(trades)
    }
}

impl MarketBarQuery for BinanceAlphaTradingRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &MarketBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        let mut bars = Vec::new();
        for symbol in &request.symbols {
            let value = self
                .service
                .public_get(
                    "/bapi/defi/v1/public/alpha-trade/klines",
                    &[
                        ("symbol", symbol.as_str().into()),
                        ("interval", request.interval.clone()),
                        ("limit", "500".into()),
                    ],
                )
                .await?;
            bars.extend(market::bars(symbol, &request.interval, data(&value))?);
        }
        Ok(bars)
    }
}

impl MarketOrderBookQuery for BinanceAlphaTradingRestConnection {
    async fn fetch_order_books(
        &mut self,
        request: &MarketOrderBookRequest,
    ) -> Result<Vec<MarketOrderBook>, IntegrationError> {
        let mut books = Vec::new();
        for symbol in &request.symbols {
            let value = self
                .service
                .public_get(
                    "/bapi/defi/v1/public/alpha-trade/fullDepth",
                    &[
                        ("symbol", symbol.as_str().into()),
                        ("limit", request.depth.unwrap_or(100).to_string()),
                    ],
                )
                .await?;
            books.push(market::book(symbol, data(&value))?);
        }
        Ok(books)
    }
}

fn data(value: &Value) -> &Value {
    value.get("data").unwrap_or(value)
}

fn first(value: &Value) -> Result<&Value, IntegrationError> {
    value
        .as_array()
        .and_then(|rows| rows.first())
        .or_else(|| value.get("ticker"))
        .or(Some(value))
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance Alpha data is missing".into()))
}

fn participant() -> ParticipantRef {
    ParticipantRef::new(ParticipantKind::Exchange, "binance").expect("static Binance participant")
}
