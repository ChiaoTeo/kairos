use kairos_primitives::{Currency, ParticipantSymbol, UnixNanos};

use crate::participants::okx::OkxRestConfig;
use crate::services::participants::okx::rest::{RestService, map_error};
use crate::services::participants::okx::{check_okx_response, market};
use crate::{
    ConnectionDescriptor, ExternalInstrument, ExternalInstrumentCatalog,
    ExternalInstrumentCatalogPage, ExternalInstrumentKind, InstrumentCatalogQuery,
    IntegrationError, MarketBar, MarketBarQuery, MarketBarRequest, MarketFundingRate,
    MarketFundingRateQuery, MarketGreeks, MarketGreeksQuery, MarketIndexPrice,
    MarketIndexPriceQuery, MarketMarkPrice, MarketMarkPriceQuery, MarketOpenInterest,
    MarketOpenInterestQuery, MarketOrderBook, MarketOrderBookQuery, MarketOrderBookRequest,
    MarketQuote, MarketQuoteQuery, MarketTrade, MarketTradeQuery, ParticipantKind, ParticipantRef,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxSystemStatus {
    pub id: String,
    pub title: String,
    pub state: String,
    pub service_type: String,
    pub begin_unix_nanos: UnixNanos,
    pub end_unix_nanos: Option<UnixNanos>,
    pub href: Option<String>,
    pub scheduled: bool,
    pub schedule_description: Option<String>,
    pub environment: Option<String>,
}

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

impl MarketMarkPriceQuery for OkxPublicRestConnection {
    async fn fetch_mark_prices(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketMarkPrice>, IntegrationError> {
        let mut values = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let value = self
                .service
                .public_get(
                    "/api/v5/public/mark-price",
                    &[
                        ("instType", derivative_instrument_type(symbol)?.into()),
                        ("instId", symbol.as_str().into()),
                    ],
                )
                .await?;
            values.push(market::mark_price(symbol, &value)?);
        }
        Ok(values)
    }
}

impl MarketIndexPriceQuery for OkxPublicRestConnection {
    async fn fetch_index_prices(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketIndexPrice>, IntegrationError> {
        let mut values = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let value = self
                .service
                .public_get(
                    "/api/v5/market/index-tickers",
                    &[("instId", index_symbol(symbol)?)],
                )
                .await?;
            values.push(market::index_price(symbol, &value)?);
        }
        Ok(values)
    }
}

impl MarketFundingRateQuery for OkxPublicRestConnection {
    async fn fetch_funding_rates(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketFundingRate>, IntegrationError> {
        let mut values = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            if derivative_instrument_type(symbol)? != "SWAP" {
                return Err(IntegrationError::InvalidRequest(format!(
                    "OKX funding rate requires a SWAP instrument: {symbol}"
                )));
            }
            let value = self
                .service
                .public_get(
                    "/api/v5/public/funding-rate",
                    &[("instId", symbol.as_str().into())],
                )
                .await?;
            values.push(market::funding_rate(symbol, &value)?);
        }
        Ok(values)
    }
}

impl MarketOpenInterestQuery for OkxPublicRestConnection {
    async fn fetch_open_interest(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketOpenInterest>, IntegrationError> {
        let mut values = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let value = self
                .service
                .public_get(
                    "/api/v5/public/open-interest",
                    &[
                        ("instType", derivative_instrument_type(symbol)?.into()),
                        ("instId", symbol.as_str().into()),
                    ],
                )
                .await?;
            values.push(market::open_interest(symbol, &value)?);
        }
        Ok(values)
    }
}

impl MarketGreeksQuery for OkxPublicRestConnection {
    async fn fetch_greeks(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketGreeks>, IntegrationError> {
        let mut values = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            if derivative_instrument_type(symbol)? != "OPTION" {
                return Err(IntegrationError::InvalidRequest(format!(
                    "OKX Greeks require an OPTION instrument: {symbol}"
                )));
            }
            let value = self
                .service
                .public_get(
                    "/api/v5/public/opt-summary",
                    &[("instFamily", index_symbol(symbol)?)],
                )
                .await?;
            values.push(market::greeks(symbol, &value)?);
        }
        Ok(values)
    }
}

fn derivative_instrument_type(
    symbol: &ParticipantSymbol,
) -> Result<&'static str, IntegrationError> {
    let parts = symbol.as_str().split('-').collect::<Vec<_>>();
    if parts.last() == Some(&"SWAP") {
        Ok("SWAP")
    } else if matches!(parts.last(), Some(&"C") | Some(&"P")) && parts.len() >= 5 {
        Ok("OPTION")
    } else if parts.len() >= 3 {
        Ok("FUTURES")
    } else {
        Err(IntegrationError::InvalidRequest(format!(
            "OKX derivative instrument is required: {symbol}"
        )))
    }
}

fn index_symbol(symbol: &ParticipantSymbol) -> Result<String, IntegrationError> {
    let mut parts = symbol.as_str().split('-');
    let base = parts.next().unwrap_or_default();
    let quote = parts.next().unwrap_or_default();
    if base.is_empty() || quote.is_empty() {
        return Err(IntegrationError::InvalidRequest(format!(
            "OKX instrument does not identify an index family: {symbol}"
        )));
    }
    Ok(format!("{base}-{quote}"))
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
                )));
            },
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
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: OkxRestConfig,
    ) -> Result<Self, IntegrationError> {
        Ok(Self {
            service: RestService::new(connection_key, config, "public.rest", None)?,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    pub fn rate_limit_headers(&self) -> std::collections::BTreeMap<String, String> {
        self.service.rate_limit_headers()
    }

    pub fn clock_health(&self) -> Result<crate::ProviderClockHealth, IntegrationError> {
        self.service.clock_health()
    }

    pub fn endpoint(&self) -> &str {
        self.service.endpoint()
    }

    pub async fn fetch_system_status(
        &mut self,
        state: Option<&str>,
    ) -> Result<Vec<OkxSystemStatus>, IntegrationError> {
        let query = state
            .filter(|value| !value.trim().is_empty())
            .map(|value| vec![("state", value.to_owned())])
            .unwrap_or_default();
        let value = self
            .service
            .public_get("/api/v5/system/status", &query)
            .await?;
        okx_system_statuses(&value)
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

fn okx_system_statuses(
    value: &serde_json::Value,
) -> Result<Vec<OkxSystemStatus>, IntegrationError> {
    value
        .get("data")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("OKX system status data is missing".into())
        })?
        .iter()
        .map(|row| {
            let text = |field: &str| {
                row.get(field)
                    .and_then(serde_json::Value::as_str)
                    .filter(|value| !value.is_empty())
            };
            let required = |field: &str| {
                text(field).ok_or_else(|| {
                    IntegrationError::InvalidPayload(format!(
                        "OKX system status {field} is missing"
                    ))
                })
            };
            let millis = |field: &str| -> Result<Option<UnixNanos>, IntegrationError> {
                text(field)
                    .map(|value| {
                        value
                            .parse::<u64>()
                            .map(|value| UnixNanos::from(value.saturating_mul(1_000_000)))
                            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
                    })
                    .transpose()
            };
            Ok(OkxSystemStatus {
                id: required("id")?.to_owned(),
                title: required("title")?.to_owned(),
                state: required("state")?.to_owned(),
                service_type: required("serviceType")?.to_owned(),
                begin_unix_nanos: millis("begin")?.ok_or_else(|| {
                    IntegrationError::InvalidPayload("OKX system status begin is missing".into())
                })?,
                end_unix_nanos: millis("end")?,
                href: text("href").map(str::to_owned),
                scheduled: required("state")?.eq_ignore_ascii_case("scheduled"),
                schedule_description: text("scheDesc").map(str::to_owned),
                environment: text("env").map(str::to_owned),
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derivative_routing_is_explicit_by_okx_symbol_family() {
        let swap = ParticipantSymbol::new("BTC-USDT-SWAP").unwrap();
        let future = ParticipantSymbol::new("BTC-USDT-260925").unwrap();
        let option = ParticipantSymbol::new("BTC-USD-260925-100000-C").unwrap();
        let spot = ParticipantSymbol::new("BTC-USDT").unwrap();

        assert_eq!(derivative_instrument_type(&swap).unwrap(), "SWAP");
        assert_eq!(derivative_instrument_type(&future).unwrap(), "FUTURES");
        assert_eq!(derivative_instrument_type(&option).unwrap(), "OPTION");
        assert!(derivative_instrument_type(&spot).is_err());
        assert_eq!(index_symbol(&option).unwrap(), "BTC-USD");
    }

    #[test]
    fn system_status_fixture_preserves_global_maintenance_window() {
        let status = okx_system_statuses(&serde_json::json!({"data":[{
            "id":"1","title":"Trading maintenance","state":"scheduled",
            "serviceType":"1","begin":"1720000000000","end":"1720003600000",
            "href":"https://www.okx.com/support/notice","scheDesc":"scheduled upgrade",
            "env":"1"
        }]}))
        .unwrap()
        .remove(0);

        assert!(status.scheduled);
        assert_eq!(status.service_type, "1");
        assert_eq!(
            status.schedule_description.as_deref(),
            Some("scheduled upgrade")
        );
        assert_eq!(status.begin_unix_nanos.get(), 1_720_000_000_000_000_000);
    }
}
