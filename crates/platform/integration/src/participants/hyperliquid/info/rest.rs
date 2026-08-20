use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::reference::Currency;
use kairos_primitives::time::UnixNanos;
use serde_json::{Value, json};

use crate::participants::hyperliquid::HyperliquidRestConfig;
use crate::services::participants::hyperliquid::rest::RestService;
use crate::{
    ConnectionDescriptor, ExternalInstrument, ExternalInstrumentCatalog,
    ExternalInstrumentCatalogPage, ExternalInstrumentKind, HistoricalBarQuery,
    HistoricalBarRequest, InstrumentCatalogQuery, IntegrationError, MarketBar, MarketFundingRate,
    MarketFundingRateQuery, MarketMarkPrice, MarketMarkPriceQuery, MarketOpenInterest,
    MarketOpenInterestQuery, MarketOrderBook, MarketOrderBookQuery, MarketQuote, MarketQuoteQuery,
    ParticipantKind, ParticipantRef,
};

pub struct HyperliquidInfoRestConnection {
    service: RestService,
}

impl HyperliquidInfoRestConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: HyperliquidRestConfig,
    ) -> Result<Self, IntegrationError> {
        Ok(Self {
            service: RestService::new(connection_key, config, "info.rest", None)?,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    pub fn endpoint(&self) -> &str {
        self.service.endpoint()
    }

    async fn info(&mut self, request: Value) -> Result<Value, IntegrationError> {
        let endpoint = self.service.endpoint().to_owned();
        self.service
            .client()
            .post_query_json_with_headers(&endpoint, &[], &request)
            .await
            .map_err(|error| IntegrationError::Transport(error.to_string()))
    }

    async fn contexts(&mut self) -> Result<Value, IntegrationError> {
        self.info(json!({"type": "metaAndAssetCtxs"})).await
    }

    pub async fn fetch_perpetual_instruments(
        &mut self,
    ) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        Ok(ExternalInstrumentCatalog {
            participant: participant(),
            instruments: perpetual_instruments(&self.contexts().await?)?,
        })
    }

    pub async fn fetch_spot_instruments(
        &mut self,
    ) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        Ok(ExternalInstrumentCatalog {
            participant: participant(),
            instruments: spot_instruments(
                &self.info(json!({"type": "spotMetaAndAssetCtxs"})).await?,
            )?,
        })
    }
}

impl InstrumentCatalogQuery for HyperliquidInfoRestConnection {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let mut instruments = self.fetch_perpetual_instruments().await?.instruments;
        instruments.extend(self.fetch_spot_instruments().await?.instruments);
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

impl MarketQuoteQuery for HyperliquidInfoRestConnection {
    async fn fetch_quotes(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        let mids = self.info(json!({"type": "allMids"})).await?;
        let mids = mids.as_object().ok_or_else(|| {
            IntegrationError::InvalidPayload(
                "Hyperliquid allMids response must be an object".into(),
            )
        })?;
        symbols
            .iter()
            .map(|symbol| {
                let price = mids
                    .get(symbol.as_str())
                    .and_then(Value::as_str)
                    .map(str::parse)
                    .transpose()
                    .map_err(payload)?;
                Ok(MarketQuote {
                    symbol: symbol.clone(),
                    bid_price: price,
                    bid_quantity: None,
                    ask_price: price,
                    ask_quantity: None,
                    last_price: price,
                    observed_at_unix_nanos: now_nanos(),
                })
            })
            .collect()
    }
}

impl MarketOrderBookQuery for HyperliquidInfoRestConnection {
    async fn fetch_order_books(
        &mut self,
        request: &crate::MarketOrderBookRequest,
    ) -> Result<Vec<MarketOrderBook>, IntegrationError> {
        let mut books = Vec::with_capacity(request.symbols.len());
        for symbol in &request.symbols {
            let value = self
                .info(json!({"type": "l2Book", "coin": symbol.as_str()}))
                .await?;
            let levels = value
                .get("levels")
                .and_then(Value::as_array)
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload("Hyperliquid l2Book levels are missing".into())
                })?;
            let bids = book_side(levels.first())?;
            let asks = book_side(levels.get(1))?;
            books.push(MarketOrderBook {
                symbol: symbol.clone(),
                bids,
                asks,
                sequence: None,
                observed_at_unix_nanos: millis(value.get("time").and_then(Value::as_u64)),
            });
        }
        Ok(books)
    }
}

impl MarketMarkPriceQuery for HyperliquidInfoRestConnection {
    async fn fetch_mark_prices(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketMarkPrice>, IntegrationError> {
        let contexts = context_rows(&self.contexts().await?)?;
        symbols
            .iter()
            .filter_map(|symbol| {
                contexts
                    .iter()
                    .find(|(name, _)| name == symbol.as_str())
                    .map(|(_, row)| {
                        Ok(MarketMarkPrice {
                            symbol: symbol.clone(),
                            price: required_price(row, "markPx")?,
                            observed_at_unix_nanos: now_nanos(),
                        })
                    })
            })
            .collect()
    }
}

impl MarketFundingRateQuery for HyperliquidInfoRestConnection {
    async fn fetch_funding_rates(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketFundingRate>, IntegrationError> {
        let contexts = context_rows(&self.contexts().await?)?;
        symbols
            .iter()
            .filter_map(|symbol| {
                contexts
                    .iter()
                    .find(|(name, _)| name == symbol.as_str())
                    .map(|(_, row)| {
                        Ok(MarketFundingRate {
                            symbol: symbol.clone(),
                            rate: required_rate(row, "funding")?,
                            next_funding_at_unix_nanos: None,
                            observed_at_unix_nanos: now_nanos(),
                        })
                    })
            })
            .collect()
    }
}

impl MarketOpenInterestQuery for HyperliquidInfoRestConnection {
    async fn fetch_open_interest(
        &mut self,
        symbols: &[ParticipantSymbol],
    ) -> Result<Vec<MarketOpenInterest>, IntegrationError> {
        let contexts = context_rows(&self.contexts().await?)?;
        symbols
            .iter()
            .filter_map(|symbol| {
                contexts
                    .iter()
                    .find(|(name, _)| name == symbol.as_str())
                    .map(|(_, row)| {
                        Ok(MarketOpenInterest {
                            symbol: symbol.clone(),
                            quantity: required_quantity(row, "openInterest")?,
                            observed_at_unix_nanos: now_nanos(),
                        })
                    })
            })
            .collect()
    }
}

impl HistoricalBarQuery for HyperliquidInfoRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &HistoricalBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let value = self
            .info(json!({
                "type": "candleSnapshot",
                "req": {
                    "coin": request.window.symbol.as_str(),
                    "interval": request.interval,
                    "startTime": request.window.start_time_unix_nanos.get() / 1_000_000,
                    "endTime": request.window.end_time_unix_nanos.get() / 1_000_000,
                }
            }))
            .await?;
        value
            .as_array()
            .ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Hyperliquid candle snapshot must be an array".into(),
                )
            })?
            .iter()
            .map(|row| {
                Ok(MarketBar {
                    symbol: request.window.symbol.clone(),
                    interval: row
                        .get("i")
                        .and_then(Value::as_str)
                        .unwrap_or(request.interval.as_str())
                        .into(),
                    open: required_price(row, "o")?,
                    high: required_price(row, "h")?,
                    low: required_price(row, "l")?,
                    close: required_price(row, "c")?,
                    volume: row
                        .get("v")
                        .and_then(Value::as_str)
                        .map(str::parse)
                        .transpose()
                        .map_err(payload)?,
                    opened_at_unix_nanos: millis(row.get("t").and_then(Value::as_u64)),
                    closed_at_unix_nanos: row
                        .get("T")
                        .and_then(Value::as_u64)
                        .map(|value| UnixNanos::from(value.saturating_mul(1_000_000))),
                    adjusted: None,
                    derivation: "participant".into(),
                })
            })
            .collect()
    }
}

fn perpetual_instruments(value: &Value) -> Result<Vec<ExternalInstrument>, IntegrationError> {
    let universe = value
        .as_array()
        .and_then(|rows| rows.first())
        .and_then(|meta| meta.get("universe"))
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid perpetual universe is missing".into())
        })?;
    universe
        .iter()
        .map(|row| {
            let name = row.get("name").and_then(Value::as_str).ok_or_else(|| {
                IntegrationError::InvalidPayload("Hyperliquid perpetual name is missing".into())
            })?;
            Ok(ExternalInstrument {
                source_symbol: ParticipantSymbol::new(name)
                    .map_err(|e| IntegrationError::InvalidPayload(e.to_string()))?,
                source_venue: None,
                kind: ExternalInstrumentKind::Perpetual,
                base_currency: Currency::new(name).ok(),
                quote_currency: Currency::new("USDC").ok(),
                settlement_currency: Currency::new("USDC").ok(),
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: !row
                    .get("isDelisted")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                price_tick: None,
                quantity_tick: decimal_tick(row.get("szDecimals").and_then(Value::as_u64)),
                minimum_quantity: None,
                minimum_notional: None,
                contract_value: None,
                price_precision: None,
                quantity_precision: row
                    .get("szDecimals")
                    .and_then(Value::as_u64)
                    .and_then(|v| u32::try_from(v).ok()),
            })
        })
        .collect()
}

fn spot_instruments(value: &Value) -> Result<Vec<ExternalInstrument>, IntegrationError> {
    let rows = value.as_array().ok_or_else(|| {
        IntegrationError::InvalidPayload(
            "Hyperliquid spot metadata response must be an array".into(),
        )
    })?;
    let meta = rows.first().ok_or_else(|| {
        IntegrationError::InvalidPayload("Hyperliquid spot metadata is missing".into())
    })?;
    let tokens = meta
        .get("tokens")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid spot tokens are missing".into())
        })?;
    let universe = meta
        .get("universe")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid spot universe is missing".into())
        })?;
    universe
        .iter()
        .map(|row| {
            let name = row.get("name").and_then(Value::as_str).ok_or_else(|| {
                IntegrationError::InvalidPayload("Hyperliquid spot name is missing".into())
            })?;
            let indexes = row.get("tokens").and_then(Value::as_array).ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Hyperliquid spot token indexes are missing".into(),
                )
            })?;
            let token = |offset: usize| {
                indexes
                    .get(offset)
                    .and_then(Value::as_u64)
                    .and_then(|index| tokens.get(index as usize))
            };
            let base = token(0).and_then(|v| v.get("name")).and_then(Value::as_str);
            let quote = token(1).and_then(|v| v.get("name")).and_then(Value::as_str);
            Ok(ExternalInstrument {
                source_symbol: ParticipantSymbol::new(name)
                    .map_err(|e| IntegrationError::InvalidPayload(e.to_string()))?,
                source_venue: None,
                kind: ExternalInstrumentKind::Spot,
                base_currency: base.map(Currency::new).transpose().map_err(payload)?,
                quote_currency: quote.map(Currency::new).transpose().map_err(payload)?,
                settlement_currency: None,
                underlying: None,
                expiry_unix_nanos: None,
                strike: None,
                option_right: None,
                active: true,
                price_tick: None,
                quantity_tick: token(0)
                    .and_then(|v| v.get("szDecimals"))
                    .and_then(Value::as_u64)
                    .and_then(|value| decimal_tick(Some(value))),
                minimum_quantity: None,
                minimum_notional: None,
                contract_value: None,
                price_precision: None,
                quantity_precision: token(0)
                    .and_then(|v| v.get("szDecimals"))
                    .and_then(Value::as_u64)
                    .and_then(|v| u32::try_from(v).ok()),
            })
        })
        .collect()
}

fn context_rows(value: &Value) -> Result<Vec<(String, Value)>, IntegrationError> {
    let rows = value.as_array().ok_or_else(|| {
        IntegrationError::InvalidPayload("Hyperliquid contexts response must be an array".into())
    })?;
    let universe = rows
        .first()
        .and_then(|v| v.get("universe"))
        .and_then(Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid universe is missing".into())
        })?;
    let contexts = rows.get(1).and_then(Value::as_array).ok_or_else(|| {
        IntegrationError::InvalidPayload("Hyperliquid asset contexts are missing".into())
    })?;
    Ok(universe
        .iter()
        .zip(contexts)
        .filter_map(|(meta, context)| {
            meta.get("name")
                .and_then(Value::as_str)
                .map(|name| (name.into(), context.clone()))
        })
        .collect())
}

fn book_side(
    value: Option<&Value>,
) -> Result<
    Vec<(
        kairos_primitives::decimal::Price,
        kairos_primitives::decimal::Quantity,
    )>,
    IntegrationError,
> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|row| Ok((required_price(row, "px")?, required_quantity(row, "sz")?)))
        .collect()
}

fn required_price(
    row: &Value,
    field: &str,
) -> Result<kairos_primitives::decimal::Price, IntegrationError> {
    required(row, field)
}
fn required_quantity(
    row: &Value,
    field: &str,
) -> Result<kairos_primitives::decimal::Quantity, IntegrationError> {
    required(row, field)
}
fn required_rate(
    row: &Value,
    field: &str,
) -> Result<kairos_primitives::decimal::Rate, IntegrationError> {
    required(row, field)
}
fn required<T: std::str::FromStr>(row: &Value, field: &str) -> Result<T, IntegrationError>
where
    T::Err: std::fmt::Display,
{
    row.get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Hyperliquid {field} is missing")))?
        .parse()
        .map_err(payload)
}
fn decimal_tick(decimals: Option<u64>) -> Option<String> {
    decimals.map(|value| {
        if value == 0 {
            "1".into()
        } else {
            format!("0.{}1", "0".repeat(value.saturating_sub(1) as usize))
        }
    })
}
fn participant() -> ParticipantRef {
    ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid")
        .expect("static Hyperliquid participant")
}
fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}
fn millis(value: Option<u64>) -> UnixNanos {
    value
        .map(|v| UnixNanos::from(v.saturating_mul(1_000_000)))
        .unwrap_or_else(now_nanos)
}
fn now_nanos() -> UnixNanos {
    use std::time::{SystemTime, UNIX_EPOCH};
    let value = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|v| v.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(value).unwrap_or(u64::MAX))
}
