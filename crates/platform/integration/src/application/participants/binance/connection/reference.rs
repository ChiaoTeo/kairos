//! Public and principal-scoped Binance reference catalog capabilities.

use super::*;
use crate::application::{
    AsyncMarketQuoteConnection, AsyncMarketSnapshotConnection, MarketEvent, MarketEventKind,
    MarketQuote,
};
use kairos_primitives::ProviderSymbol;

pub struct BinanceInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) instrument_type: InstrumentType,
    pub(super) base_url: String,
    pub(super) runtime: BinanceRequestRuntime,
}

pub struct BinanceEquityInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) base_url: String,
    pub(super) api_key: SecretString,
    pub(super) runtime: BinanceRequestRuntime,
}

pub struct BinanceEquityMarketQuote {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) base_url: String,
    pub(super) api_key: SecretString,
    pub(super) runtime: BinanceRequestRuntime,
}

impl BinanceInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl BinanceEquityInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl BinanceEquityMarketQuote {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncInstrumentCatalogConnection for BinanceInstrumentCatalog {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        self.runtime
            .acquire(1, RequestPriority::Background)
            .map_err(map_exchange_error)?;
        let response = self
            .runtime
            .async_http()
            .get_json_response_with_headers_and_query(
                &format!("{}{}", self.base_url, self.instrument_type.path()),
                &[],
                &[],
            )
            .await
            .map_err(map_exchange_error)?;
        self.runtime.observe_response(&response);
        match self.instrument_type {
            InstrumentType::Spot => instrument_catalog::normalize_spot(&response.body),
            InstrumentType::UsdMFutures | InstrumentType::CoinMFutures => {
                instrument_catalog::normalize_derivatives(&response.body)
            }
            InstrumentType::Option => instrument_catalog::normalize_options(&response.body),
        }
    }
}

impl AsyncInstrumentCatalogConnection for BinanceEquityInstrumentCatalog {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        self.runtime
            .acquire(1, RequestPriority::Background)
            .map_err(map_exchange_error)?;
        let response = self
            .runtime
            .async_http()
            .get_json_response_with_headers_and_query(
                &format!(
                    "{}/sapi/v1/equity/market/exchangeInfo",
                    self.base_url.trim_end_matches('/')
                ),
                &[],
                &[("X-MBX-APIKEY", self.api_key.expose_secret().to_owned())],
            )
            .await
            .map_err(map_exchange_error)?;
        self.runtime.observe_response(&response);
        equity_catalog::normalize_catalog(&response.body)
    }
}

impl AsyncMarketQuoteConnection for BinanceEquityMarketQuote {
    async fn fetch_quote(
        &mut self,
        symbol: &ProviderSymbol,
    ) -> Result<Option<MarketQuote>, IntegrationError> {
        let symbol = symbol.as_str().trim().to_ascii_uppercase();
        if symbol.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Equity quote symbol is required".into(),
            ));
        }
        self.runtime
            .acquire(1, RequestPriority::Background)
            .map_err(map_exchange_error)?;
        let response = self
            .runtime
            .async_http()
            .get_json_response_with_headers_and_query(
                &format!(
                    "{}/sapi/v1/equity/market/quote",
                    self.base_url.trim_end_matches('/')
                ),
                &[("symbol", symbol.clone())],
                &[("X-MBX-APIKEY", self.api_key.expose_secret().to_owned())],
            )
            .await
            .map_err(map_exchange_error)?;
        self.runtime.observe_response(&response);
        let symbol = ProviderSymbol::new(symbol)
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        equity_catalog::normalize_quote(&response.body, &symbol)
    }
}

impl AsyncMarketSnapshotConnection for BinanceEquityMarketQuote {
    async fn fetch_snapshot(
        &mut self,
        symbols: &[ProviderSymbol],
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        let mut events = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let Some(quote) = self.fetch_quote(symbol).await? else {
                continue;
            };
            events.push(MarketEvent {
                symbol: quote.symbol,
                kind: MarketEventKind::Quote,
                price: quote.bid_price,
                quantity: quote.bid_quantity,
                rate: None,
                ask_price: quote.ask_price,
                ask_quantity: quote.ask_quantity,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: None,
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: None,
                observed_at_unix_nanos: quote.observed_at_unix_nanos,
                venue: Default::default(),
            });
        }
        Ok(events)
    }
}
