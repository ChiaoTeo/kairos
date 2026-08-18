//! Massive Forex/Crypto reference and historical market-data connection.

use kairos_primitives::{Currency, ParticipantSymbol, Price, Quantity};
use secrecy::ExposeSecret;

use crate::services::participants::massive::currencies::{
    CurrenciesRestService, CurrencyBarRow, CurrencyMarket, CurrencyQuoteRow, CurrencyTickerRow,
    CurrencyTradeRow,
};
use crate::{
    ConnectionDescriptor, ExternalInstrument, ExternalInstrumentCatalog,
    ExternalInstrumentCatalogPage, ExternalInstrumentKind, HistoricalBarQuery,
    HistoricalBarRequest, HistoricalQuoteQuery, HistoricalTradeQuery, HistoricalWindow,
    InstrumentCatalogQuery, IntegrationError, MarketBar, MarketQuote, MarketTrade, ParticipantKind,
    ParticipantRef,
};

use super::{rest::map_exchange_error, MassiveCurrenciesRestConfig, MassiveCurrencyMarket};

pub struct MassiveCurrenciesRestConnection {
    descriptor: ConnectionDescriptor,
    market: MassiveCurrencyMarket,
    service: CurrenciesRestService,
}

impl MassiveCurrenciesRestConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: MassiveCurrenciesRestConfig,
    ) -> Result<Self, IntegrationError> {
        let mut descriptor = ConnectionDescriptor::new(
            connection_key,
            ParticipantRef::new(ParticipantKind::DataProvider, "massive")
                .map_err(IntegrationError::InvalidRequest)?,
            config.market.domain(),
        )
        .map_err(IntegrationError::InvalidRequest)?;
        descriptor.environment = config.environment;
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let service_market = match config.market {
            MassiveCurrencyMarket::Forex => CurrencyMarket::Forex,
            MassiveCurrencyMarket::Crypto => CurrencyMarket::Crypto,
        };
        Ok(Self {
            descriptor,
            market: config.market,
            service: CurrenciesRestService::new(
                config.api_key.expose_secret(),
                config.endpoint,
                service_market,
                config.as_of,
            )
            .map_err(map_exchange_error)?,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl InstrumentCatalogQuery for MassiveCurrenciesRestConnection {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let instruments = self
            .service
            .tickers()
            .await
            .map_err(map_exchange_error)?
            .into_iter()
            .map(normalize_ticker)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(ExternalInstrumentCatalog {
            participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive")
                .expect("static Massive participant"),
            instruments,
        })
    }

    async fn fetch_instruments_page(
        &mut self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<ExternalInstrumentCatalogPage, IntegrationError> {
        let offset = cursor.unwrap_or("0").parse::<usize>().map_err(|_| {
            IntegrationError::InvalidRequest("invalid Massive catalog cursor".into())
        })?;
        let limit = limit.clamp(1, 1000);
        let rows = self.service.tickers().await.map_err(map_exchange_error)?;
        let total = rows.len();
        let instruments = rows
            .into_iter()
            .skip(offset)
            .take(limit)
            .map(normalize_ticker)
            .collect::<Result<Vec<_>, _>>()?;
        let next_offset = offset.saturating_add(instruments.len());
        let complete = next_offset >= total;
        Ok(ExternalInstrumentCatalogPage {
            catalog: ExternalInstrumentCatalog {
                participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive")
                    .expect("static Massive participant"),
                instruments,
            },
            next_cursor: (!complete).then(|| next_offset.to_string()),
            complete,
        })
    }
}

impl HistoricalBarQuery for MassiveCurrenciesRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &HistoricalBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let (multiplier, timespan) =
            crate::services::participants::massive::market::data::parse_interval(
                &request.interval,
            )?;
        self.service
            .bars(
                request.window.symbol.as_str(),
                multiplier,
                timespan,
                request.window.start_time_unix_nanos.get() / 1_000_000,
                request.window.end_time_unix_nanos.get() / 1_000_000,
            )
            .await
            .map_err(map_exchange_error)?
            .into_iter()
            .map(|row| normalize_bar(row, request, self.market))
            .collect()
    }
}

impl HistoricalQuoteQuery for MassiveCurrenciesRestConnection {
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

impl HistoricalTradeQuery for MassiveCurrenciesRestConnection {
    async fn fetch_trades(
        &mut self,
        window: &HistoricalWindow,
    ) -> Result<Vec<MarketTrade>, IntegrationError> {
        if self.market != MassiveCurrencyMarket::Crypto {
            return Err(IntegrationError::UnsupportedOperation);
        }
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

fn normalize_ticker(row: CurrencyTickerRow) -> Result<ExternalInstrument, IntegrationError> {
    Ok(ExternalInstrument {
        source_symbol: ParticipantSymbol::new(row.ticker)
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        source_venue: row.source_venue,
        kind: ExternalInstrumentKind::Spot,
        base_currency: row
            .base_currency
            .map(Currency::new)
            .transpose()
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        quote_currency: row
            .quote_currency
            .map(Currency::new)
            .transpose()
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        settlement_currency: None,
        underlying: None,
        expiry_unix_nanos: None,
        strike: None,
        option_right: None,
        active: row.active,
        price_tick: None,
        quantity_tick: None,
        minimum_quantity: None,
        minimum_notional: None,
        contract_value: None,
        price_precision: None,
        quantity_precision: None,
    })
}

fn normalize_bar(
    row: CurrencyBarRow,
    request: &HistoricalBarRequest,
    market: MassiveCurrencyMarket,
) -> Result<MarketBar, IntegrationError> {
    Ok(MarketBar {
        symbol: request.window.symbol.clone(),
        interval: request.interval.clone(),
        open: parse(&row.open)?,
        high: parse(&row.high)?,
        low: parse(&row.low)?,
        close: parse(&row.close)?,
        volume: row.volume.as_deref().map(parse_quantity).transpose()?,
        opened_at_unix_nanos: row.opened_at_unix_millis.saturating_mul(1_000_000).into(),
        closed_at_unix_nanos: None,
        adjusted: request.adjusted,
        derivation: match market {
            MassiveCurrencyMarket::Forex => "massive-forex-quote-aggregate".into(),
            MassiveCurrencyMarket::Crypto => "massive-crypto-trade-aggregate".into(),
        },
    })
}

fn normalize_quote(
    row: CurrencyQuoteRow,
    window: &HistoricalWindow,
) -> Result<MarketQuote, IntegrationError> {
    Ok(MarketQuote {
        symbol: window.symbol.clone(),
        bid_price: row.bid_price.as_deref().map(parse).transpose()?,
        bid_quantity: row.bid_size.as_deref().map(parse_quantity).transpose()?,
        ask_price: row.ask_price.as_deref().map(parse).transpose()?,
        ask_quantity: row.ask_size.as_deref().map(parse_quantity).transpose()?,
        last_price: None,
        observed_at_unix_nanos: row.timestamp_unix_nanos.into(),
    })
}

fn normalize_trade(
    row: CurrencyTradeRow,
    window: &HistoricalWindow,
) -> Result<MarketTrade, IntegrationError> {
    Ok(MarketTrade {
        symbol: window.symbol.clone(),
        participant_trade_id: row.trade_id,
        price: parse(&row.price)?,
        quantity: parse_quantity(&row.size)?,
        is_buyer_maker: None,
        event_at_unix_nanos: row.timestamp_unix_nanos.into(),
    })
}

fn parse(value: &str) -> Result<Price, IntegrationError> {
    value
        .parse()
        .map_err(|error: kairos_primitives::DomainTypeError| {
            IntegrationError::InvalidPayload(error.to_string())
        })
}

fn parse_quantity(value: &str) -> Result<Quantity, IntegrationError> {
    value
        .parse()
        .map_err(|error: kairos_primitives::DomainTypeError| {
            IntegrationError::InvalidPayload(error.to_string())
        })
}
