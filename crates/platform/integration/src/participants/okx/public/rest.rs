use crate::participants::okx::OkxRestConfig;
use crate::services::participants::okx::{
    check_okx_response, market,
    rest::{map_error, RestService},
};
use crate::{
    ConnectionDescriptor, ExternalInstrument, ExternalInstrumentCatalog,
    ExternalInstrumentCatalogPage, ExternalInstrumentKind, InstrumentCatalogQuery,
    IntegrationError, MarketBar, MarketBarQuery, MarketBarRequest, MarketOrderBook,
    MarketOrderBookQuery, MarketOrderBookRequest, MarketQuote, MarketQuoteQuery, MarketTrade,
    MarketTradeQuery, ParticipantKind, ParticipantRef,
};
use kairos_primitives::{Currency, ParticipantSymbol, UnixNanos};

pub struct OkxPublicRestConnection {
    service: RestService,
}

impl InstrumentCatalogQuery for OkxPublicRestConnection {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let mut instruments = Vec::new();
        for instrument_type in ["SPOT", "MARGIN", "SWAP", "FUTURES", "OPTION"] {
            instruments.extend(
                self.fetch_instruments_by_type(instrument_type)
                    .await?
                    .instruments,
            );
        }
        Ok(ExternalInstrumentCatalog {
            participant: participant(),
            instruments,
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
        if limit > 0 && catalog.instruments.len() > limit {
            catalog.instruments.truncate(limit);
        }
        Ok(ExternalInstrumentCatalogPage {
            catalog,
            next_cursor: None,
            complete: true,
        })
    }
}

impl MarketQuoteQuery for OkxPublicRestConnection {
    async fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        let mut quotes = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let endpoint = format!("{}/api/v5/market/ticker", self.service.endpoint());
            let response = self
                .service
                .client()
                .get_json_response_with_headers_and_query(
                    &endpoint,
                    &[("instId", symbol.as_str().to_string())],
                    &[],
                )
                .await
                .map(|response| response.body)
                .and_then(check_okx_response)
                .map_err(map_error)?;
            let row = response
                .get("data")
                .and_then(serde_json::Value::as_array)
                .and_then(|rows| rows.first())
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload("OKX ticker data is missing".into())
                })?;
            quotes.push(market::quote(symbol, row)?);
        }
        Ok(quotes)
    }
}

impl MarketTradeQuery for OkxPublicRestConnection {
    async fn fetch_trades(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketTrade>, IntegrationError> {
        let mut trades = Vec::new();
        for symbol in symbols {
            let value = self
                .service
                .public_get(
                    "/api/v5/market/trades",
                    &[("instId", symbol.as_str().into()), ("limit", "100".into())],
                )
                .await?;
            trades.extend(market::trades(symbol, &value)?);
        }
        Ok(trades)
    }
}

impl MarketBarQuery for OkxPublicRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &MarketBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        let mut bars = Vec::new();
        for symbol in &request.symbols {
            let value = self
                .service
                .public_get(
                    "/api/v5/market/candles",
                    &[
                        ("instId", symbol.as_str().into()),
                        ("bar", request.interval.clone()),
                        ("limit", "300".into()),
                    ],
                )
                .await?;
            bars.extend(market::bars(symbol, &request.interval, &value)?);
        }
        Ok(bars)
    }
}

impl MarketOrderBookQuery for OkxPublicRestConnection {
    async fn fetch_order_books(
        &mut self,
        request: &MarketOrderBookRequest,
    ) -> Result<Vec<MarketOrderBook>, IntegrationError> {
        let mut books = Vec::new();
        for symbol in &request.symbols {
            let value = self
                .service
                .public_get(
                    "/api/v5/market/books",
                    &[
                        ("instId", symbol.as_str().into()),
                        ("sz", request.depth.unwrap_or(100).min(400).to_string()),
                    ],
                )
                .await?;
            books.push(market::book(symbol, &value)?);
        }
        Ok(books)
    }
}

fn normalize_instrument(
    instrument_type: &str,
    row: &serde_json::Value,
) -> Result<ExternalInstrument, IntegrationError> {
    let text = |field: &str| {
        row.get(field)
            .and_then(serde_json::Value::as_str)
            .filter(|v| !v.is_empty())
    };
    let source_symbol = text("instId")
        .ok_or_else(|| IntegrationError::InvalidPayload("OKX instrument id is missing".into()))?;
    Ok(ExternalInstrument {
        source_symbol: ParticipantSymbol::new(source_symbol)
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        source_venue: None,
        kind: match instrument_type {
            "SPOT" => ExternalInstrumentKind::Spot,
            "MARGIN" => ExternalInstrumentKind::Margin,
            "SWAP" => ExternalInstrumentKind::Perpetual,
            "FUTURES" => ExternalInstrumentKind::Future,
            "OPTION" => ExternalInstrumentKind::Option,
            _ => {
                return Err(IntegrationError::InvalidPayload(format!(
                    "unsupported OKX instrument type {instrument_type}"
                )))
            }
        },
        base_currency: currency(text("baseCcy"))?,
        quote_currency: currency(text("quoteCcy"))?,
        settlement_currency: currency(text("settleCcy"))?,
        underlying: text("uly")
            .map(ParticipantSymbol::new)
            .transpose()
            .map_err(|e| IntegrationError::InvalidPayload(e.to_string()))?,
        expiry_unix_nanos: text("expTime")
            .and_then(|v| v.parse::<u64>().ok())
            .map(|v| UnixNanos::from(v.saturating_mul(1_000_000))),
        strike: text("stk").map(str::to_owned),
        option_right: text("optType").map(str::to_owned),
        active: text("state").is_none_or(|value| value == "live"),
        price_tick: text("tickSz").map(str::to_owned),
        quantity_tick: text("lotSz").map(str::to_owned),
        minimum_quantity: text("minSz").map(str::to_owned),
        minimum_notional: None,
        contract_value: text("ctVal").map(str::to_owned),
        price_precision: None,
        quantity_precision: None,
    })
}

fn participant() -> ParticipantRef {
    ParticipantRef::new(ParticipantKind::Exchange, "okx").expect("static OKX participant")
}

fn currency(value: Option<&str>) -> Result<Option<Currency>, IntegrationError> {
    value
        .map(Currency::new)
        .transpose()
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

impl OkxPublicRestConnection {
    pub fn new(config: OkxRestConfig) -> Result<Self, IntegrationError> {
        Ok(Self {
            service: RestService::new(config, "public.rest", None)?,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    pub fn endpoint(&self) -> &str {
        self.service.endpoint()
    }

    /// Provider-native bounded catalog query used when composition enables a
    /// single OKX product family. The participant-neutral capability remains
    /// the full catalog query.
    pub async fn fetch_instruments_by_type(
        &mut self,
        instrument_type: &str,
    ) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let instrument_type = instrument_type.trim().to_ascii_uppercase();
        if !["SPOT", "MARGIN", "SWAP", "FUTURES", "OPTION"].contains(&instrument_type.as_str()) {
            return Err(IntegrationError::InvalidRequest(format!(
                "unsupported OKX instrument type: {instrument_type}"
            )));
        }
        let endpoint = format!("{}/api/v5/public/instruments", self.service.endpoint());
        let response = self
            .service
            .client()
            .get_json_response_with_headers_and_query(
                &endpoint,
                &[("instType", instrument_type.clone())],
                &[],
            )
            .await
            .map(|response| response.body)
            .and_then(check_okx_response)
            .map_err(map_error)?;
        let rows = response
            .get("data")
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| {
                IntegrationError::InvalidPayload("OKX instrument data is missing".into())
            })?;
        Ok(ExternalInstrumentCatalog {
            participant: participant(),
            instruments: rows
                .iter()
                .map(|row| normalize_instrument(&instrument_type, row))
                .collect::<Result<Vec<_>, _>>()?,
        })
    }
}
