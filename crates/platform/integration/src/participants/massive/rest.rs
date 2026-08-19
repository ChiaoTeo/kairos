use std::str::FromStr;

use secrecy::ExposeSecret;

use super::{
    InstrumentQuery, InstrumentType, MassiveCashDividend, MassiveOptionSnapshot, MassiveRestConfig,
};
use crate::services::participants::massive::market::data::{
    MarketType as ServiceMarketType, normalize_historical, normalize_historical_quotes,
    normalize_historical_trades, parse_interval,
};
use crate::services::participants::massive::{RestService, reference as normalization};
use crate::transport::http::ExchangeError;
use crate::{
    ConnectionDescriptor, ExternalInstrumentCatalog, ExternalInstrumentCatalogPage,
    HistoricalBarQuery, HistoricalBarRequest, HistoricalQuoteQuery, HistoricalTradeQuery,
    HistoricalWindow, InstrumentCatalogQuery, IntegrationError, MarketBar, MarketGreeks,
    MarketGreeksQuery, MarketQuote, MarketTrade, ParticipantKind, ParticipantRef,
};

pub struct MassiveRestConnection {
    descriptor: ConnectionDescriptor,
    service: RestService,
    instrument_query: InstrumentQuery,
}

impl MassiveRestConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: MassiveRestConfig,
    ) -> Result<Self, IntegrationError> {
        let mut descriptor = ConnectionDescriptor::new(
            connection_key,
            ParticipantRef::new(ParticipantKind::DataProvider, "massive")
                .map_err(IntegrationError::InvalidRequest)?,
            "market-data.rest",
        )
        .map_err(IntegrationError::InvalidRequest)?;
        descriptor.environment = config.environment;
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let service = configure_service(
            RestService::with_base_url(config.api_key.expose_secret(), config.endpoint)
                .map_err(map_exchange_error)?,
            &config.instrument_query,
        );
        Ok(Self {
            descriptor,
            service,
            instrument_query: config.instrument_query,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub async fn fetch_dividends(
        &mut self,
        ticker: &str,
        start_date: &str,
        end_date: &str,
    ) -> Result<Vec<MassiveCashDividend>, IntegrationError> {
        self.service
            .cash_dividends(ticker, start_date, end_date)
            .await
            .map_err(map_exchange_error)
            .map(|rows| {
                rows.into_iter()
                    .map(|row| MassiveCashDividend {
                        id: row.id,
                        ticker: row.ticker,
                        ex_dividend_date: row.ex_dividend_date,
                        declaration_date: row.declaration_date,
                        record_date: row.record_date,
                        pay_date: row.pay_date,
                        cash_amount: row.cash_amount,
                        split_adjusted_cash_amount: row.split_adjusted_cash_amount,
                        historical_adjustment_factor: row.historical_adjustment_factor,
                        currency: row.currency,
                        distribution_type: row.distribution_type,
                        frequency: row.frequency,
                    })
                    .collect()
            })
    }

    pub async fn fetch_option_snapshot(
        &mut self,
        symbol: &kairos_primitives::ParticipantSymbol,
    ) -> Result<MassiveOptionSnapshot, IntegrationError> {
        if self.instrument_query.instrument_type != InstrumentType::Option {
            return Err(IntegrationError::InvalidRequest(
                "Massive option snapshots require an options REST connection".into(),
            ));
        }
        let underlying = self.instrument_query.underlying.as_deref().ok_or_else(|| {
            IntegrationError::InvalidRequest(
                "Massive option snapshots require a configured underlying".into(),
            )
        })?;
        let row = self
            .service
            .option_snapshot(underlying, symbol.as_str())
            .await
            .map_err(map_exchange_error)?;
        if row.ticker != symbol.as_str() {
            return Err(IntegrationError::InvalidPayload(format!(
                "Massive option snapshot returned {} for requested {symbol}",
                row.ticker
            )));
        }
        let expiry_unix_nanos = row
            .expiration_date
            .as_deref()
            .map(parse_expiry)
            .transpose()?;
        Ok(MassiveOptionSnapshot {
            symbol: symbol.clone(),
            values: crate::Greeks {
                expiry_unix_nanos,
                strike: parse_optional(row.strike_price)?,
                delta: parse_optional(row.delta)?,
                gamma: parse_optional(row.gamma)?,
                vega: parse_optional(row.vega)?,
                theta: parse_optional(row.theta)?,
                implied_volatility: parse_optional(row.implied_volatility)?,
                derivation: "massive-options-snapshot".into(),
            },
            break_even_price: parse_optional(row.break_even_price)?,
            open_interest: parse_optional(row.open_interest)?,
            market_status: row.market_status,
            observed_at_unix_nanos: row
                .observed_at_unix_nanos
                .unwrap_or_else(now_unix_nanos)
                .into(),
        })
    }

    fn market_type(&self) -> ServiceMarketType {
        match self.instrument_query.instrument_type {
            InstrumentType::Equity => ServiceMarketType::Equity,
            InstrumentType::Option => ServiceMarketType::Option,
        }
    }
}

impl MarketGreeksQuery for MassiveRestConnection {
    async fn fetch_greeks(
        &mut self,
        symbols: &[kairos_primitives::ParticipantSymbol],
    ) -> Result<Vec<MarketGreeks>, IntegrationError> {
        let mut values = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let snapshot = self.fetch_option_snapshot(symbol).await?;
            values.push(MarketGreeks {
                symbol: snapshot.symbol,
                values: snapshot.values,
                observed_at_unix_nanos: snapshot.observed_at_unix_nanos,
            });
        }
        Ok(values)
    }
}

fn parse_optional<T>(value: Option<String>) -> Result<Option<T>, IntegrationError>
where
    T: FromStr,
    T::Err: std::fmt::Display,
{
    value
        .map(|value| {
            value
                .parse::<T>()
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
        })
        .transpose()
}

fn parse_expiry(value: &str) -> Result<kairos_primitives::UnixNanos, IntegrationError> {
    let date = chrono::NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
    let timestamp = date
        .and_hms_opt(0, 0, 0)
        .and_then(|value| value.and_utc().timestamp_nanos_opt())
        .ok_or_else(|| IntegrationError::InvalidPayload("invalid Massive option expiry".into()))?;
    u64::try_from(timestamp).map(Into::into).map_err(|_| {
        IntegrationError::InvalidPayload("Massive option expiry predates epoch".into())
    })
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}

impl InstrumentCatalogQuery for MassiveRestConnection {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        normalization::normalize(
            self.service
                .load_markets()
                .await
                .map_err(map_exchange_error)?,
        )
    }

    async fn fetch_instruments_page(
        &mut self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<ExternalInstrumentCatalogPage, IntegrationError> {
        let page = self
            .service
            .load_markets_page(cursor, limit)
            .await
            .map_err(map_exchange_error)?;
        Ok(ExternalInstrumentCatalogPage {
            catalog: normalization::normalize(page.rows)?,
            next_cursor: page.next_cursor,
            complete: page.complete,
        })
    }
}

impl HistoricalBarQuery for MassiveRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &HistoricalBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let (multiplier, timespan) = parse_interval(&request.interval)?;
        let rows = self
            .service
            .historical_bars(
                request.window.symbol.as_str(),
                multiplier,
                timespan,
                (request.window.start_time_unix_nanos.get() / 1_000_000) as i64,
                (request.window.end_time_unix_nanos.get() / 1_000_000) as i64,
                request.adjusted.unwrap_or(false),
            )
            .await
            .map_err(map_exchange_error)?;
        normalize_historical(rows, request, &request.interval, self.market_type())
    }
}

impl HistoricalQuoteQuery for MassiveRestConnection {
    async fn fetch_quotes(
        &mut self,
        window: &HistoricalWindow,
    ) -> Result<Vec<MarketQuote>, IntegrationError> {
        window
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let rows = self
            .service
            .historical_quotes(
                window.symbol.as_str(),
                window.start_time_unix_nanos.get(),
                window.end_time_unix_nanos.get(),
            )
            .await
            .map_err(map_exchange_error)?;
        normalize_historical_quotes(rows, window)
    }
}

impl HistoricalTradeQuery for MassiveRestConnection {
    async fn fetch_trades(
        &mut self,
        window: &HistoricalWindow,
    ) -> Result<Vec<MarketTrade>, IntegrationError> {
        window
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let rows = self
            .service
            .historical_trades(
                window.symbol.as_str(),
                window.start_time_unix_nanos.get(),
                window.end_time_unix_nanos.get(),
            )
            .await
            .map_err(map_exchange_error)?;
        normalize_historical_trades(rows, window)
    }
}

fn configure_service(mut service: RestService, query: &InstrumentQuery) -> RestService {
    service = match query.instrument_type {
        InstrumentType::Equity => service.for_equity(),
        InstrumentType::Option => service.for_options(),
    };
    if let Some(value) = &query.underlying {
        service = service.with_option_underlying(value.clone());
    }
    if let Some(value) = &query.as_of {
        service = service.with_option_as_of(value.clone());
    }
    if let (Some(start), Some(end)) = (&query.expiration_date_gte, &query.expiration_date_lte) {
        service = service.with_option_expiration_range(start.clone(), end.clone());
    }
    if let Some(value) = &query.contract_type {
        service = service.with_option_contract_type(value.clone());
    }
    service
}

pub(crate) fn map_exchange_error(error: ExchangeError) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::Http {
            status: 401, body, ..
        } => IntegrationError::Authentication(body),
        ExchangeError::Http {
            status: 403, body, ..
        } => {
            let normalized = body.to_ascii_lowercase();
            if ["plan", "subscription", "entitlement", "not included"]
                .iter()
                .any(|needle| normalized.contains(needle))
            {
                IntegrationError::Entitlement(body)
            } else {
                IntegrationError::Authorization(body)
            }
        },
        ExchangeError::Http {
            status: 429, body, ..
        } => IntegrationError::RateLimited(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}

#[cfg(test)]
mod error_tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use super::*;

    #[test]
    fn plan_denial_is_entitlement_not_transport() {
        let error = map_exchange_error(ExchangeError::Http {
            status: 403,
            body: "futures plan does not include this endpoint".into(),
            metadata: crate::transport::http::HttpResponseMetadata::default(),
        });

        assert!(matches!(error, IntegrationError::Entitlement(_)));
    }

    #[test]
    fn generic_forbidden_response_remains_authorization() {
        let error = map_exchange_error(ExchangeError::Http {
            status: 403,
            body: "access forbidden".into(),
            metadata: crate::transport::http::HttpResponseMetadata::default(),
        });

        assert!(matches!(error, IntegrationError::Authorization(_)));
    }

    #[tokio::test]
    async fn option_snapshot_maps_partial_analytics_without_inventing_missing_values() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0_u8; 8192];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            assert!(
                request
                    .lines()
                    .next()
                    .unwrap_or_default()
                    .contains("/v3/snapshot/options/SPY/O:SPY260821C00500000")
            );
            assert!(
                request
                    .to_ascii_lowercase()
                    .contains("authorization: bearer test-secret\r\n")
            );
            let body = r#"{"results":{"details":{"ticker":"O:SPY260821C00500000","expiration_date":"2026-08-21","strike_price":500},"break_even_price":503.25,"open_interest":42,"greeks":{"delta":0.55,"gamma":0.007,"theta":-0.018},"implied_volatility":0.304,"market_status":"open","last_quote":{"last_updated":1787270400000000000}}}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });
        let mut connection = MassiveRestConnection::new(
            crate::ConnectionKey::new("market.massive.options.test").unwrap(),
            MassiveRestConfig {
                environment: "test".into(),
                endpoint,
                api_key: secrecy::SecretString::from("test-secret".to_owned()),
                instrument_query: InstrumentQuery::options(Some("SPY".into())),
            },
        )
        .unwrap();
        let symbol = kairos_primitives::ParticipantSymbol::new("O:SPY260821C00500000").unwrap();

        let snapshot = connection.fetch_option_snapshot(&symbol).await.unwrap();

        server.join().unwrap();
        assert_eq!(snapshot.symbol, symbol);
        assert_eq!(snapshot.values.delta.unwrap().to_string(), "0.55");
        assert_eq!(snapshot.values.vega, None);
        assert_eq!(
            snapshot.values.implied_volatility.unwrap().to_string(),
            "0.304"
        );
        assert_eq!(snapshot.break_even_price.unwrap().to_string(), "503.25");
        assert_eq!(snapshot.open_interest.unwrap().to_string(), "42");
        assert_eq!(snapshot.market_status.as_deref(), Some("open"));
        assert_eq!(
            snapshot.observed_at_unix_nanos.get(),
            1_787_270_400_000_000_000
        );
    }
}
