//! Massive Futures reference and historical market-data connection.

use kairos_primitives::integration::ParticipantSymbol;
use kairos_primitives::time::UnixNanos;
use secrecy::ExposeSecret;

use super::MassiveFuturesRestConfig;
use super::rest::map_exchange_error;
use crate::services::participants::massive::futures::{
    FuturesBarRow, FuturesContractRow, FuturesQuoteRow, FuturesRestService, FuturesTradeRow,
};
use crate::{
    ConnectionDescriptor, ExternalInstrument, ExternalInstrumentCatalog,
    ExternalInstrumentCatalogPage, ExternalInstrumentKind, HistoricalBarQuery,
    HistoricalBarRequest, HistoricalQuoteQuery, HistoricalTradeQuery, HistoricalWindow,
    InstrumentCatalogQuery, IntegrationError, MarketBar, MarketQuote, MarketTrade, ParticipantKind,
    ParticipantRef,
};

pub struct MassiveFuturesRestConnection {
    descriptor: ConnectionDescriptor,
    service: FuturesRestService,
}

impl MassiveFuturesRestConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: MassiveFuturesRestConfig,
    ) -> Result<Self, IntegrationError> {
        let mut descriptor = ConnectionDescriptor::new(
            connection_key,
            ParticipantRef::new(ParticipantKind::DataProvider, "massive")
                .map_err(IntegrationError::InvalidRequest)?,
            "futures.rest",
        )
        .map_err(IntegrationError::InvalidRequest)?;
        descriptor.environment = config.environment;
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            descriptor,
            service: FuturesRestService::new(
                config.api_key.expose_secret(),
                config.endpoint,
                config.product_code,
                config.as_of,
            )
            .map_err(map_exchange_error)?,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl InstrumentCatalogQuery for MassiveFuturesRestConnection {
    async fn fetch_instruments(&self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let mut cursor = None;
        let mut instruments = Vec::new();
        for _ in 0..10_000 {
            let page = self
                .service
                .contracts_page(cursor.as_deref(), 1000)
                .await
                .map_err(map_exchange_error)?;
            instruments.extend(
                page.rows
                    .into_iter()
                    .map(normalize_contract)
                    .collect::<Result<Vec<_>, _>>()?,
            );
            cursor = page.next_cursor;
            if cursor.is_none() {
                return Ok(catalog(instruments));
            }
        }
        Err(IntegrationError::InvalidPayload(
            "Massive Futures contract pagination exceeded safety limit".into(),
        ))
    }

    async fn fetch_instruments_page(
        &self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<ExternalInstrumentCatalogPage, IntegrationError> {
        let page = self
            .service
            .contracts_page(cursor, limit)
            .await
            .map_err(map_exchange_error)?;
        let next_cursor = page.next_cursor;
        Ok(ExternalInstrumentCatalogPage {
            catalog: catalog(
                page.rows
                    .into_iter()
                    .map(normalize_contract)
                    .collect::<Result<Vec<_>, _>>()?,
            ),
            complete: next_cursor.is_none(),
            next_cursor,
        })
    }
}

impl HistoricalBarQuery for MassiveFuturesRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &HistoricalBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let resolution = futures_resolution(&request.interval)?;
        self.service
            .bars(
                request.window.symbol.as_str(),
                &resolution,
                request.window.start_time_unix_nanos.get(),
                request.window.end_time_unix_nanos.get(),
            )
            .await
            .map_err(map_exchange_error)?
            .into_iter()
            .map(|row| normalize_bar(row, request))
            .collect()
    }
}

impl HistoricalQuoteQuery for MassiveFuturesRestConnection {
    async fn fetch_quotes(
        &mut self,
        window: &HistoricalWindow,
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        window
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        self.service
            .quotes(
                window.symbol.as_str(),
                window.start_time_unix_nanos.get(),
                window.end_time_unix_nanos.get(),
            )
            .await
            .map_err(map_exchange_error)?
            .into_iter()
            .map(|row| normalize_quote(row, window))
            .collect()
    }
}

impl HistoricalTradeQuery for MassiveFuturesRestConnection {
    async fn fetch_trades(
        &mut self,
        window: &HistoricalWindow,
    ) -> Result<Vec<MarketTrade>, IntegrationError> {
        window
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        self.service
            .trades(
                window.symbol.as_str(),
                window.start_time_unix_nanos.get(),
                window.end_time_unix_nanos.get(),
            )
            .await
            .map_err(map_exchange_error)?
            .into_iter()
            .map(|row| normalize_trade(row, window))
            .collect()
    }
}

fn normalize_contract(row: FuturesContractRow) -> Result<ExternalInstrument, IntegrationError> {
    Ok(ExternalInstrument {
        source_symbol: ParticipantSymbol::new(row.ticker)
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        source_venue: row.trading_venue,
        kind: ExternalInstrumentKind::Future,
        base_currency: None,
        quote_currency: None,
        settlement_currency: None,
        underlying: row
            .product_code
            .map(ParticipantSymbol::new)
            .transpose()
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        expiry_unix_nanos: row
            .settlement_date
            .as_deref()
            .and_then(date_to_unix_nanos)
            .map(UnixNanos::new),
        strike: None,
        option_right: None,
        active: row.active,
        price_tick: row.trade_tick_size,
        quantity_tick: Some("1".into()),
        minimum_quantity: row.min_order_quantity,
        minimum_notional: None,
        contract_value: None,
        price_precision: None,
        quantity_precision: Some(0),
    })
}

fn catalog(instruments: Vec<ExternalInstrument>) -> ExternalInstrumentCatalog {
    ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive")
            .expect("static Massive participant"),
        instruments,
        venues: Vec::new(),
    }
}

fn normalize_bar(
    row: FuturesBarRow,
    request: &HistoricalBarRequest,
) -> Result<MarketBar, IntegrationError> {
    Ok(MarketBar {
        symbol: request.window.symbol.clone(),
        interval: request.interval.clone(),
        open: parse(&row.open)?,
        high: parse(&row.high)?,
        low: parse(&row.low)?,
        close: parse(&row.close)?,
        volume: row.volume.as_deref().map(parse).transpose()?,
        opened_at_unix_nanos: row.window_start_unix_nanos.into(),
        closed_at_unix_nanos: None,
        adjusted: None,
        derivation: "massive-futures-aggregate".into(),
    })
}

fn normalize_quote(
    row: FuturesQuoteRow,
    window: &HistoricalWindow,
) -> Result<MarketQuote, IntegrationError> {
    Ok(MarketQuote {
        venue: Default::default(),
        symbol: window.symbol.clone(),
        bid_price: row.bid_price.as_deref().map(parse).transpose()?,
        bid_quantity: row.bid_size.as_deref().map(parse).transpose()?,
        ask_price: row.ask_price.as_deref().map(parse).transpose()?,
        ask_quantity: row.ask_size.as_deref().map(parse).transpose()?,
        last_price: None,
        observed_at_unix_nanos: row.timestamp_unix_nanos.into(),
    })
}

fn normalize_trade(
    row: FuturesTradeRow,
    window: &HistoricalWindow,
) -> Result<MarketTrade, IntegrationError> {
    Ok(MarketTrade {
        symbol: window.symbol.clone(),
        participant_trade_id: row.sequence_number.map(|value| value.to_string()),
        price: parse(&row.price)?,
        quantity: parse(&row.size)?,
        is_buyer_maker: None,
        event_at_unix_nanos: row.timestamp_unix_nanos.into(),
    })
}

fn parse<T>(value: &str) -> Result<T, IntegrationError>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    value
        .parse::<T>()
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

fn futures_resolution(interval: &str) -> Result<String, IntegrationError> {
    let interval = interval.trim().to_ascii_lowercase();
    let split = interval
        .find(|character: char| !character.is_ascii_digit())
        .ok_or_else(|| IntegrationError::InvalidRequest("invalid futures interval".into()))?;
    let multiplier = interval[..split]
        .parse::<u32>()
        .map_err(|_| IntegrationError::InvalidRequest("invalid futures interval".into()))?;
    if multiplier == 0 {
        return Err(IntegrationError::InvalidRequest(
            "futures interval must be positive".into(),
        ));
    }
    let unit = match &interval[split..] {
        "s" | "sec" | "second" => "sec",
        "m" | "min" | "minute" => "min",
        "h" | "hour" => "hour",
        "d" | "day" | "session" => "session",
        "w" | "week" => "week",
        other => {
            return Err(IntegrationError::InvalidRequest(format!(
                "unsupported Massive Futures interval unit: {other}"
            )));
        },
    };
    Ok(format!("{multiplier}{unit}"))
}

fn date_to_unix_nanos(value: &str) -> Option<u64> {
    let mut parts = value.split('-');
    let year: i64 = parts.next()?.parse().ok()?;
    let month: i64 = parts.next()?.parse().ok()?;
    let day: i64 = parts.next()?.parse().ok()?;
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    let adjusted_year = year - i64::from(month <= 2);
    let era = if adjusted_year >= 0 {
        adjusted_year / 400
    } else {
        (adjusted_year - 399) / 400
    };
    let year_of_era = adjusted_year - era * 400;
    let adjusted_month = month + if month > 2 { -3 } else { 9 };
    let day_of_year = (153 * adjusted_month + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    let days = era * 146097 + day_of_era - 719468;
    u64::try_from(days.checked_mul(86_400)?)
        .ok()?
        .checked_mul(1_000_000_000)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn futures_resolution_uses_session_not_equity_day_semantics() {
        assert_eq!(futures_resolution("1m").unwrap(), "1min");
        assert_eq!(futures_resolution("1d").unwrap(), "1session");
        assert!(futures_resolution("1month").is_err());
    }

    #[test]
    fn contract_normalization_keeps_product_and_venue_as_provider_facts() {
        let instrument = normalize_contract(FuturesContractRow {
            ticker: "GCJ5".into(),
            product_code: Some("GC".into()),
            trading_venue: Some("XNYM".into()),
            active: true,
            settlement_date: Some("2025-04-28".into()),
            trade_tick_size: Some("0.1".into()),
            min_order_quantity: Some("1".into()),
        })
        .unwrap();

        assert_eq!(instrument.kind, ExternalInstrumentKind::Future);
        assert_eq!(instrument.source_venue.as_deref(), Some("XNYM"));
        assert_eq!(instrument.underlying.unwrap().as_str(), "GC");
        assert!(instrument.expiry_unix_nanos.is_some());
    }
}
