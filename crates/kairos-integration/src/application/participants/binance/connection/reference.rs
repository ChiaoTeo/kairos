//! Public and principal-scoped Binance reference catalog capabilities.

use super::*;

pub struct BinanceInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) instrument_type: InstrumentType,
    pub(super) base_url: String,
    pub(super) runtime: BinanceSpotProviderRuntime,
}

pub struct BinanceEquityInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) base_url: String,
    pub(super) api_key: SecretString,
    pub(super) runtime: BinanceSpotProviderRuntime,
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
