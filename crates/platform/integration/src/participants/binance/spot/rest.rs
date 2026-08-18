use crate::services::participants::binance::{account, execution, market};
use crate::{
    AccountQuery, CommandResult, ExternalAccountSegment, ExternalAccountSnapshot,
    ExternalInstrumentCatalog, ExternalInstrumentCatalogPage, ExternalOrder, ExternalOrderQuery,
    HistoricalBarQuery, HistoricalBarRequest, HistoricalQuoteQuery, HistoricalTradeQuery,
    HistoricalWindow, InstrumentCatalogQuery, IntegrationError, MarketBar, MarketBarQuery,
    MarketBarRequest, MarketOrderBook, MarketOrderBookQuery, MarketOrderBookRequest, MarketQuote,
    MarketQuoteQuery, MarketTrade, MarketTradeQuery, OrderCommand, OrderEntryEvent,
    OrderEntryRequest, OrderQuery, ParticipantKind, ParticipantRef,
};
use kairos_primitives::ParticipantSymbol;

rest_connection!(BinanceSpotRestConnection, "spot.rest");

impl BinanceSpotRestConnection {
    /// Reduces an order quantity without losing queue priority. Binance Spot
    /// does not expose a general price/quantity modify operation here.
    pub async fn amend_order_keep_priority(
        &mut self,
        request: &crate::participants::binance::BinanceAmendOrderRequest,
    ) -> CommandResult<OrderEntryEvent> {
        let params = [
            (
                "symbol",
                request
                    .replacement
                    .participant_instrument
                    .source_symbol
                    .to_string(),
            ),
            ("orderId", request.remote_order_id.clone()),
            ("newQty", execution::decimal(request.replacement.quantity)),
        ];
        let outcome = self
            .service
            .signed_put_command("/api/v3/order/amend/keepPriority", &params)
            .await?;
        execution::submitted_outcome(&request.replacement, outcome)
    }

    pub async fn cancel_all_open_orders(
        &mut self,
        scope: &crate::participants::binance::BinanceCancelAllScope,
    ) -> CommandResult<crate::participants::binance::BinanceCancelAllScope> {
        match self
            .service
            .signed_delete_command(
                "/api/v3/openOrders",
                &[("symbol", scope.symbol.to_string())],
            )
            .await?
        {
            crate::CommandOutcome::Confirmed(_) => {
                Ok(crate::CommandOutcome::Confirmed(scope.clone()))
            }
            crate::CommandOutcome::Rejected(error) => Ok(crate::CommandOutcome::Rejected(error)),
            crate::CommandOutcome::Indeterminate(error) => {
                Ok(crate::CommandOutcome::Indeterminate(error))
            }
        }
    }

    pub async fn fetch_account_trades(
        &mut self,
        query: &crate::participants::binance::BinanceHistoryQuery,
    ) -> Result<Vec<crate::participants::binance::BinanceTradeRecord>, IntegrationError> {
        let payload = self
            .service
            .signed_get("/api/v3/myTrades", &query.params(true, false)?)
            .await?;
        crate::participants::binance::history::trades(&payload)
    }
}

impl InstrumentCatalogQuery for BinanceSpotRestConnection {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let value = self.service.public_get("/api/v3/exchangeInfo", &[]).await?;
        Ok(ExternalInstrumentCatalog {
            participant: participant(),
            instruments: market::spot_instruments(&value)?,
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
impl MarketQuoteQuery for BinanceSpotRestConnection {
    async fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        let mut values = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let value = self
                .service
                .public_get(
                    "/api/v3/ticker/bookTicker",
                    &[("symbol", symbol.as_str().into())],
                )
                .await?;
            values.push(market::quote(symbol, &value)?)
        }
        Ok(values)
    }
}
impl MarketTradeQuery for BinanceSpotRestConnection {
    async fn fetch_trades(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketTrade>, IntegrationError> {
        let mut values = Vec::new();
        for symbol in symbols {
            let value = self
                .service
                .public_get(
                    "/api/v3/trades",
                    &[("symbol", symbol.as_str().into()), ("limit", "100".into())],
                )
                .await?;
            values.extend(market::trades(symbol, &value)?)
        }
        Ok(values)
    }
}
impl MarketBarQuery for BinanceSpotRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &MarketBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        let mut values = Vec::new();
        for symbol in &request.symbols {
            let value = self
                .service
                .public_get(
                    "/api/v3/klines",
                    &[
                        ("symbol", symbol.as_str().into()),
                        ("interval", request.interval.clone()),
                        ("limit", "500".into()),
                    ],
                )
                .await?;
            values.extend(market::bars(symbol, &request.interval, &value)?)
        }
        Ok(values)
    }
}
impl MarketOrderBookQuery for BinanceSpotRestConnection {
    async fn fetch_order_books(
        &mut self,
        request: &MarketOrderBookRequest,
    ) -> Result<Vec<MarketOrderBook>, IntegrationError> {
        let mut values = Vec::new();
        for symbol in &request.symbols {
            let value = self
                .service
                .public_get(
                    "/api/v3/depth",
                    &[
                        ("symbol", symbol.as_str().into()),
                        ("limit", request.depth.unwrap_or(100).to_string()),
                    ],
                )
                .await?;
            values.push(market::book(symbol, &value)?)
        }
        Ok(values)
    }
}
impl HistoricalBarQuery for BinanceSpotRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &HistoricalBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let value = self
            .service
            .public_get(
                "/api/v3/klines",
                &[
                    ("symbol", request.window.symbol.as_str().into()),
                    ("interval", request.interval.clone()),
                    (
                        "startTime",
                        (request.window.start_time_unix_nanos.get() / 1_000_000).to_string(),
                    ),
                    (
                        "endTime",
                        (request.window.end_time_unix_nanos.get() / 1_000_000).to_string(),
                    ),
                    ("limit", "1000".into()),
                ],
            )
            .await?;
        market::bars(&request.window.symbol, &request.interval, &value)
    }
}
impl HistoricalQuoteQuery for BinanceSpotRestConnection {
    async fn fetch_quotes(
        &mut self,
        _window: &HistoricalWindow,
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        Err(IntegrationError::UnsupportedOperation)
    }
}
impl HistoricalTradeQuery for BinanceSpotRestConnection {
    async fn fetch_trades(
        &mut self,
        window: &HistoricalWindow,
    ) -> Result<Vec<MarketTrade>, IntegrationError> {
        window
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let value = self
            .service
            .public_get(
                "/api/v3/aggTrades",
                &[
                    ("symbol", window.symbol.as_str().into()),
                    (
                        "startTime",
                        (window.start_time_unix_nanos.get() / 1_000_000).to_string(),
                    ),
                    (
                        "endTime",
                        (window.end_time_unix_nanos.get() / 1_000_000).to_string(),
                    ),
                    ("limit", "1000".into()),
                ],
            )
            .await?;
        value
            .as_array()
            .ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Binance aggregate trades response must be an array".into(),
                )
            })?
            .iter()
            .map(|row| {
                let text = |field: &str| {
                    row.get(field)
                        .and_then(serde_json::Value::as_str)
                        .ok_or_else(|| {
                            IntegrationError::InvalidPayload(format!(
                                "Binance aggregate trade {field} is missing"
                            ))
                        })
                };
                Ok(MarketTrade {
                    symbol: window.symbol.clone(),
                    participant_trade_id: row
                        .get("a")
                        .and_then(serde_json::Value::as_u64)
                        .map(|value| value.to_string()),
                    price: text("p")?
                        .parse::<kairos_primitives::Price>()
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    quantity: text("q")?
                        .parse::<kairos_primitives::Quantity>()
                        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                    is_buyer_maker: row.get("m").and_then(serde_json::Value::as_bool),
                    event_at_unix_nanos: kairos_primitives::UnixNanos::from(
                        row.get("T")
                            .and_then(serde_json::Value::as_u64)
                            .unwrap_or_default()
                            .saturating_mul(1_000_000),
                    ),
                })
            })
            .collect()
    }
}
impl AccountQuery for BinanceSpotRestConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        let value = self.service.signed_get("/api/v3/account", &[]).await?;
        account::spot(segment, &value)
    }
}
impl OrderCommand for BinanceSpotRestConnection {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        let params = execution::params(request)?;
        let outcome = self
            .service
            .signed_post_command("/api/v3/order", &params)
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
            .signed_delete_command("/api/v3/order", &params)
            .await?;
        execution::canceled_outcome(request, remote_order_id, at_unix_nanos, outcome)
    }
}
impl OrderQuery for BinanceSpotRestConnection {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = query_params(query, false)?;
        let value = self
            .service
            .signed_get("/api/v3/openOrders", &params)
            .await?;
        execution::orders(&self.descriptor().connection_key, &value)
    }
    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = query_params(query, false)?;
        let value = self
            .service
            .signed_get("/api/v3/allOrders", &params)
            .await?;
        execution::orders(&self.descriptor().connection_key, &value)
    }
    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let params = query_params(query, true)?;
        let value = self.service.signed_get("/api/v3/order", &params).await?;
        Ok(
            execution::orders(&self.descriptor().connection_key, &value)?
                .into_iter()
                .next(),
        )
    }
}
fn query_params(
    query: &ExternalOrderQuery,
    detail: bool,
) -> Result<Vec<(&'static str, String)>, IntegrationError> {
    let mut values = Vec::new();
    if let Some(symbol) = &query.symbol {
        values.push(("symbol", symbol.to_string()));
    } else {
        return Err(IntegrationError::InvalidRequest(
            "Binance Spot order query requires symbol".into(),
        ));
    }
    if detail {
        if let Some(id) = &query.order_id {
            values.push(("origClientOrderId", id.to_string()));
        } else {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot order detail requires order id".into(),
            ));
        }
    }
    if let Some(limit) = query.limit {
        values.push(("limit", limit.to_string()));
    }
    Ok(values)
}
fn participant() -> ParticipantRef {
    ParticipantRef::new(ParticipantKind::Exchange, "binance").expect("static Binance participant")
}
