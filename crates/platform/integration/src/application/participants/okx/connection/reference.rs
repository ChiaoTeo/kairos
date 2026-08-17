//! Public OKX instrument catalog and market snapshot capabilities.

use super::*;

pub struct OkxInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) instrument_type: InstrumentType,
    pub(super) base_url: String,
    pub(super) http: AsyncPublicHttpClient,
}

pub struct OkxMarketSnapshot {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) base_url: String,
    pub(super) http: AsyncPublicHttpClient,
}

pub(super) fn normalize_instrument_catalog(
    instrument_type: InstrumentType,
    payload: &serde_json::Value,
) -> Result<ExternalInstrumentCatalog, IntegrationError> {
    if payload.get("code").and_then(serde_json::Value::as_str) != Some("0") {
        return Err(IntegrationError::InvalidPayload(format!(
            "OKX instrument catalog request failed: {payload}"
        )));
    }
    let rows = payload
        .get("data")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("OKX instruments response data is missing".into())
        })?;
    fn optional_text<'a>(row: &'a serde_json::Value, field: &str) -> Option<&'a str> {
        row.get(field)
            .and_then(serde_json::Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
    }
    let instruments = rows
        .iter()
        .map(|row| {
            let Some(source_symbol) = optional_text(row, "instId") else {
                if optional_text(row, "state") == Some("preopen")
                    && optional_text(row, "instFamily").is_some()
                {
                    // OKX publishes placeholder rows for announced pre-open
                    // futures before assigning an addressable instId. They are
                    // not yet Listings/Markets and become catalog records once
                    // the provider assigns the ID.
                    return Ok(None);
                }
                return Err(IntegrationError::InvalidPayload(
                    "OKX instrument id is missing".into(),
                ));
            };
            let expiry_unix_nanos = optional_text(row, "expTime")
                .and_then(|value| value.parse::<u64>().ok())
                .filter(|value| *value != 0)
                .map(|value| UnixNanos::from(value.saturating_mul(1_000_000)));
            let currency = |field| {
                optional_text(row, field)
                    .map(Currency::new)
                    .transpose()
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
            };
            Ok(Some(ExternalInstrument {
                source_symbol: ProviderSymbol::new(source_symbol)
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                source_venue: None,
                kind: match instrument_type {
                    InstrumentType::Spot => ExternalInstrumentKind::Spot,
                    InstrumentType::Margin => ExternalInstrumentKind::Margin,
                    InstrumentType::Swap => ExternalInstrumentKind::Perpetual,
                    InstrumentType::Futures => ExternalInstrumentKind::Future,
                    InstrumentType::Option => ExternalInstrumentKind::Option,
                },
                base_currency: currency("baseCcy")?,
                quote_currency: currency("quoteCcy")?,
                settlement_currency: currency("settleCcy")?,
                underlying: optional_text(row, "uly")
                    .map(ProviderSymbol::new)
                    .transpose()
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                expiry_unix_nanos,
                strike: optional_text(row, "stk").map(str::to_owned),
                option_right: optional_text(row, "optType").map(str::to_owned),
                active: optional_text(row, "state") == Some("live"),
                price_tick: optional_text(row, "tickSz").map(str::to_owned),
                quantity_tick: optional_text(row, "lotSz").map(str::to_owned),
                minimum_quantity: optional_text(row, "minSz").map(str::to_owned),
                minimum_notional: None,
                contract_value: optional_text(row, "ctVal").map(str::to_owned),
                price_precision: None,
                quantity_precision: None,
            }))
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?
        .into_iter()
        .flatten()
        .collect();
    Ok(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Exchange, "okx")
            .expect("static OKX participant"),
        instruments,
    })
}

pub(super) fn normalize_market_snapshot(
    requested: &ProviderSymbol,
    payload: &serde_json::Value,
) -> Result<MarketEvent, IntegrationError> {
    if payload.get("code").and_then(serde_json::Value::as_str) != Some("0") {
        return Err(IntegrationError::InvalidPayload(format!(
            "OKX ticker request failed: {payload}"
        )));
    }
    let row = payload
        .get("data")
        .and_then(serde_json::Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| IntegrationError::InvalidPayload("OKX ticker has no data".into()))?;
    let text = |field| {
        row.get(field)
            .and_then(serde_json::Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
    };
    let bid = parse_optional_ticker::<Price>(row, "bidPx")?;
    let ask = parse_optional_ticker::<Price>(row, "askPx")?;
    if bid.is_none() && ask.is_none() {
        return Err(IntegrationError::InvalidPayload(
            "OKX ticker has neither bid nor ask".into(),
        ));
    }
    Ok(MarketEvent {
        symbol: Symbol::new(text("instId").unwrap_or(requested.as_str()))
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        kind: MarketEventKind::Quote,
        price: bid,
        quantity: parse_optional_ticker::<Quantity>(row, "bidSz")?,
        rate: None,
        ask_price: ask,
        ask_quantity: parse_optional_ticker::<Quantity>(row, "askSz")?,
        bids: Vec::new(),
        asks: Vec::new(),
        bar: None,
        greeks: None,
        first_sequence: None,
        last_sequence: None,
        sequence: None,
        observed_at_unix_nanos: now_unix_nanos().into(),
        venue: Default::default(),
    })
}

fn parse_optional_ticker<T>(
    row: &serde_json::Value,
    field: &str,
) -> Result<Option<T>, IntegrationError>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    row.get(field)
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::parse)
        .transpose()
        .map_err(|error| IntegrationError::InvalidPayload(format!("OKX ticker {field}: {error}")))
}

impl OkxInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncInstrumentCatalogConnection for OkxInstrumentCatalog {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        if self.instrument_type == InstrumentType::Option {
            let payload = self
                .http
                .get_json_response_with_headers_and_query(
                    &format!("{}/api/v5/public/underlying", self.base_url),
                    &[("instType", "OPTION".into())],
                    &[],
                )
                .await
                .map_err(map_exchange_error)?
                .body;
            if payload.get("code").and_then(serde_json::Value::as_str) != Some("0") {
                return Err(IntegrationError::InvalidPayload(format!(
                    "OKX option underlying request failed: {payload}"
                )));
            }
            let underlyings = payload
                .get("data")
                .and_then(serde_json::Value::as_array)
                .into_iter()
                .flatten()
                .flat_map(|group| {
                    group
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(serde_json::Value::as_str)
                })
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
                .collect::<Vec<_>>();
            if underlyings.is_empty() {
                return Err(IntegrationError::InvalidPayload(
                    "OKX option underlying response is empty".into(),
                ));
            }
            let mut catalog = ExternalInstrumentCatalog {
                participant: ParticipantRef::new(ParticipantKind::Exchange, "okx")
                    .expect("static OKX participant"),
                instruments: Vec::new(),
            };
            for underlying in underlyings {
                let payload = self
                    .http
                    .get_json_response_with_headers_and_query(
                        &format!("{}/api/v5/public/instruments", self.base_url),
                        &[("instType", "OPTION".into()), ("uly", underlying.clone())],
                        &[],
                    )
                    .await
                    .map_err(map_exchange_error)?
                    .body;
                catalog.instruments.extend(
                    normalize_instrument_catalog(InstrumentType::Option, &payload)?.instruments,
                );
            }
            return Ok(catalog);
        }
        let payload = self
            .http
            .get_json_response_with_headers_and_query(
                &format!("{}/api/v5/public/instruments", self.base_url),
                &[("instType", self.instrument_type.api_value().into())],
                &[],
            )
            .await
            .map_err(map_exchange_error)?
            .body;
        normalize_instrument_catalog(self.instrument_type, &payload)
    }
}

impl OkxMarketSnapshot {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncMarketSnapshotConnection for OkxMarketSnapshot {
    async fn fetch_snapshot(
        &mut self,
        symbols: &[ProviderSymbol],
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        let mut events = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let payload = self
                .http
                .get_json_response_with_headers_and_query(
                    &format!("{}/api/v5/market/ticker", self.base_url),
                    &[("instId", symbol.as_str().to_owned())],
                    &[],
                )
                .await
                .map_err(map_exchange_error)?
                .body;
            events.push(normalize_market_snapshot(symbol, &payload)?);
        }
        Ok(events)
    }
}
