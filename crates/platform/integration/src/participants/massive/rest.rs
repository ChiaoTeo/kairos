use secrecy::ExposeSecret;

use crate::services::participants::massive::market::data::{
    normalize_historical, normalize_historical_quotes, normalize_historical_trades, parse_interval,
    MarketType as ServiceMarketType,
};
use crate::services::participants::massive::reference as normalization;
use crate::services::participants::massive::RestService;
use crate::transport::http::ExchangeError;
use crate::{
    ConnectionDescriptor, ExternalInstrumentCatalog, ExternalInstrumentCatalogPage,
    HistoricalBarQuery, HistoricalBarRequest, HistoricalQuoteQuery, HistoricalTradeQuery,
    HistoricalWindow, InstrumentCatalogQuery, IntegrationError, MarketBar, MarketQuote,
    MarketTrade, ParticipantKind, ParticipantRef,
};

use super::{InstrumentQuery, InstrumentType, MassiveCashDividend, MassiveRestConfig};

pub struct MassiveRestConnection {
    descriptor: ConnectionDescriptor,
    service: RestService,
    instrument_query: InstrumentQuery,
}

impl MassiveRestConnection {
    pub fn new(config: MassiveRestConfig) -> Result<Self, IntegrationError> {
        let mut descriptor = ConnectionDescriptor::new(
            config.binding_id,
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

    fn market_type(&self) -> ServiceMarketType {
        match self.instrument_query.instrument_type {
            InstrumentType::Equity => ServiceMarketType::Equity,
            InstrumentType::Option => ServiceMarketType::Option,
        }
    }
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
        ExchangeError::Http { status: 429, body } => IntegrationError::RateLimited(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}
