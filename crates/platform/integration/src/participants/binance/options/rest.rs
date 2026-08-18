use kairos_primitives::{ParticipantSymbol, Rate, UnixNanos};
use serde_json::Value;

use crate::services::participants::binance::{execution, market};
use crate::{
    AccountQuery, CommandResult, ExternalAccountSegment, ExternalAccountSnapshot,
    ExternalInstrumentCatalog, ExternalInstrumentCatalogPage, ExternalInstrumentKind,
    ExternalOrder, ExternalOrderQuery, Greeks, InstrumentCatalogQuery, IntegrationError, MarketBar,
    MarketBarQuery, MarketBarRequest, MarketGreeks, MarketGreeksQuery, MarketOrderBook,
    MarketOrderBookQuery, MarketOrderBookRequest, MarketQuote, MarketQuoteQuery, MarketTrade,
    MarketTradeQuery, OrderCommand, OrderEntryEvent, OrderEntryRequest, OrderQuery,
    ParticipantKind, ParticipantRef,
};

rest_connection!(BinanceOptionsRestConnection, "options.rest");

impl AccountQuery for BinanceOptionsRestConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        let value = self
            .service
            .signed_get("/eapi/v1/marginAccount", &[])
            .await?;
        crate::services::participants::binance::account::options(segment, &value)
    }
}

impl InstrumentCatalogQuery for BinanceOptionsRestConnection {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let value = self
            .service
            .public_get("/eapi/v1/exchangeInfo", &[])
            .await?;
        Ok(ExternalInstrumentCatalog {
            participant: participant(),
            instruments: market::derivative_instruments(&value, ExternalInstrumentKind::Option)?,
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

impl MarketQuoteQuery for BinanceOptionsRestConnection {
    async fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        let mut quotes = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let value = self
                .service
                .public_get("/eapi/v1/ticker", &[("symbol", symbol.as_str().into())])
                .await?;
            quotes.push(market::quote(symbol, first(&value)?)?);
        }
        Ok(quotes)
    }
}

impl MarketTradeQuery for BinanceOptionsRestConnection {
    async fn fetch_trades(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketTrade>, IntegrationError> {
        let mut trades = Vec::new();
        for symbol in symbols {
            let value = self
                .service
                .public_get(
                    "/eapi/v1/trades",
                    &[("symbol", symbol.as_str().into()), ("limit", "100".into())],
                )
                .await?;
            trades.extend(market::trades(symbol, &value)?);
        }
        Ok(trades)
    }
}

impl MarketBarQuery for BinanceOptionsRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &MarketBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        let mut bars = Vec::new();
        for symbol in &request.symbols {
            let value = self
                .service
                .public_get(
                    "/eapi/v1/klines",
                    &[
                        ("symbol", symbol.as_str().into()),
                        ("interval", request.interval.clone()),
                        ("limit", "500".into()),
                    ],
                )
                .await?;
            bars.extend(market::bars(symbol, &request.interval, &value)?);
        }
        Ok(bars)
    }
}

impl MarketOrderBookQuery for BinanceOptionsRestConnection {
    async fn fetch_order_books(
        &mut self,
        request: &MarketOrderBookRequest,
    ) -> Result<Vec<MarketOrderBook>, IntegrationError> {
        let mut books = Vec::new();
        for symbol in &request.symbols {
            let value = self
                .service
                .public_get(
                    "/eapi/v1/depth",
                    &[
                        ("symbol", symbol.as_str().into()),
                        ("limit", request.depth.unwrap_or(100).to_string()),
                    ],
                )
                .await?;
            books.push(market::book(symbol, &value)?);
        }
        Ok(books)
    }
}

impl MarketGreeksQuery for BinanceOptionsRestConnection {
    async fn fetch_greeks(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketGreeks>, IntegrationError> {
        let mut values = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let value = self
                .service
                .public_get("/eapi/v1/ticker", &[("symbol", symbol.as_str().into())])
                .await?;
            let row = first(&value)?;
            values.push(MarketGreeks {
                symbol: symbol.clone(),
                values: Greeks {
                    expiry_unix_nanos: None,
                    strike: None,
                    delta: rate(row, "delta")?,
                    gamma: rate(row, "gamma")?,
                    vega: rate(row, "vega")?,
                    theta: rate(row, "theta")?,
                    implied_volatility: rate(row, "markIV")?,
                    derivation: "participant".into(),
                },
                observed_at_unix_nanos: now(),
            });
        }
        Ok(values)
    }
}

impl OrderCommand for BinanceOptionsRestConnection {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        let params = execution::params(request)?;
        let outcome = self
            .service
            .signed_post_command("/eapi/v1/order", &params)
            .await?;
        execution::submitted_outcome(request, outcome)
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        let params = [
            (
                "symbol",
                request.participant_instrument.source_symbol.as_str().into(),
            ),
            ("orderId", remote_order_id.into()),
        ];
        let outcome = self
            .service
            .signed_delete_command("/eapi/v1/order", &params)
            .await?;
        execution::canceled_outcome(request, remote_order_id, at_unix_nanos, outcome)
    }
}

impl OrderQuery for BinanceOptionsRestConnection {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = query_params(query, false)?;
        let value = self
            .service
            .signed_get("/eapi/v1/openOrders", &params)
            .await?;
        execution::orders(&self.descriptor().binding_id, &value)
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = query_params(query, false)?;
        let value = self
            .service
            .signed_get("/eapi/v1/historyOrders", &params)
            .await?;
        execution::orders(&self.descriptor().binding_id, &value)
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let params = query_params(query, true)?;
        let value = self.service.signed_get("/eapi/v1/order", &params).await?;
        Ok(execution::orders(&self.descriptor().binding_id, &value)?
            .into_iter()
            .next())
    }
}

fn query_params(
    query: &ExternalOrderQuery,
    detail: bool,
) -> Result<Vec<(&'static str, String)>, IntegrationError> {
    let symbol = query.symbol.as_ref().ok_or_else(|| {
        IntegrationError::InvalidRequest("Binance Options order query requires symbol".into())
    })?;
    let mut values = vec![("symbol", symbol.to_string())];
    if detail {
        let order_id = query.order_id.as_ref().ok_or_else(|| {
            IntegrationError::InvalidRequest(
                "Binance Options order detail requires order id".into(),
            )
        })?;
        values.push(("clientOrderId", order_id.to_string()));
    }
    if let Some(limit) = query.limit {
        values.push(("limit", limit.to_string()));
    }
    Ok(values)
}

fn first(value: &Value) -> Result<&Value, IntegrationError> {
    value
        .as_array()
        .and_then(|rows| rows.first())
        .or_else(|| value.as_object().map(|_| value))
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance Options ticker is empty".into()))
}

fn rate(row: &Value, field: &str) -> Result<Option<Rate>, IntegrationError> {
    row.get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(|value| value.parse::<Rate>())
        .transpose()
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

fn participant() -> ParticipantRef {
    ParticipantRef::new(ParticipantKind::Exchange, "binance").expect("static Binance participant")
}

fn now() -> UnixNanos {
    use std::time::{SystemTime, UNIX_EPOCH};

    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(nanos).unwrap_or(u64::MAX))
}
