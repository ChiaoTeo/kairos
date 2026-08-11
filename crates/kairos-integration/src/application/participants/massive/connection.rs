use secrecy::ExposeSecret;

use crate::application::capabilities::{ParticipantKind, ParticipantRef};
use crate::application::{ConnectionDescriptor, ConnectionDomainRef, IntegrationError};
use crate::services::participants::massive::{MassiveAsyncRestClient, MassiveStocksRestClient};
use crate::services::transport::http::ExchangeError;

use super::config::{MassiveChannelConfig, MassiveConnectionConfig};
use super::market_data::{
    MassiveAsyncHistoricalMarket, MassiveAsyncLiveMarket, MassiveHistoricalMarket,
};
use super::reference::{blocking, MassiveInstrumentCatalog};
use super::types::{InstrumentQuery, InstrumentType, MarketType};

pub struct MassiveConnection {
    config: MassiveConnectionConfig,
    client: MassiveAsyncRestClient,
}

impl MassiveConnection {
    pub fn connect(config: MassiveConnectionConfig) -> Result<Self, IntegrationError> {
        if config.environment.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Massive environment is required".into(),
            ));
        }
        let client = MassiveAsyncRestClient::with_base_url(
            config.api_key.expose_secret(),
            config.rest_base_url.clone(),
        )
        .map_err(map_exchange_error)?;
        Ok(Self { config, client })
    }

    pub fn instrument_catalog(&self, query: InstrumentQuery) -> MassiveInstrumentCatalog {
        MassiveInstrumentCatalog {
            descriptor: self.descriptor(query.instrument_type),
            client: configure_async_client(self.client.clone(), &query),
        }
    }

    pub fn blocking_instrument_catalog(
        &self,
        query: InstrumentQuery,
    ) -> Result<blocking::MassiveInstrumentCatalog, IntegrationError> {
        let client = MassiveStocksRestClient::with_base_url(
            self.config.api_key.expose_secret(),
            self.config.rest_base_url.clone(),
        )
        .map_err(map_exchange_error)?;
        Ok(blocking::MassiveInstrumentCatalog {
            descriptor: self.descriptor(query.instrument_type),
            client: configure_blocking_client(client, &query),
        })
    }

    pub fn live_market(
        &self,
        market_type: MarketType,
        websocket_endpoint: impl Into<String>,
        channel: MassiveChannelConfig,
    ) -> Result<MassiveAsyncLiveMarket, IntegrationError> {
        Ok(MassiveAsyncLiveMarket {
            inner:
                crate::services::participants::massive::market_data::MassiveAsyncMarketStream::new(
                    self.config.api_key.expose_secret(),
                    websocket_endpoint,
                    market_type.service_type(),
                    channel.event_queue_capacity,
                )?,
        })
    }

    pub fn historical_market(
        &self,
        market_type: MarketType,
    ) -> Result<MassiveAsyncHistoricalMarket, IntegrationError> {
        Ok(MassiveAsyncHistoricalMarket {
            inner: crate::services::participants::massive::market_data::MassiveAsyncHistoricalMarketData::new(
                self.config.api_key.expose_secret(),
                self.config.rest_base_url.clone(),
                market_type.service_type(),
            )?,
        })
    }

    pub fn blocking_historical_market(
        &self,
        market_type: MarketType,
    ) -> Result<MassiveHistoricalMarket, IntegrationError> {
        Ok(MassiveHistoricalMarket {
            inner: crate::services::participants::massive::market_data::MassiveHistoricalMarketData::new(
                self.config.api_key.expose_secret(),
                self.config.rest_base_url.clone(),
                market_type.service_type(),
            )?,
        })
    }

    fn descriptor(&self, instrument_type: InstrumentType) -> ConnectionDescriptor {
        ConnectionDescriptor {
            binding_id: format!("massive.public.{}", instrument_type.as_str()),
            participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive")
                .expect("static Massive participant"),
            environment: self.config.environment.clone(),
            principal_id: None,
            domain: ConnectionDomainRef::new("market-data")
                .expect("static Massive connection domain"),
        }
    }
}

fn configure_async_client(
    mut client: MassiveAsyncRestClient,
    query: &InstrumentQuery,
) -> MassiveAsyncRestClient {
    client = match query.instrument_type {
        InstrumentType::Equity => client.for_equity(),
        InstrumentType::Option => client.for_options(),
    };
    if let Some(underlying) = &query.underlying {
        client = client.with_option_underlying(underlying.clone());
    }
    client
}

fn configure_blocking_client(
    mut client: MassiveStocksRestClient,
    query: &InstrumentQuery,
) -> MassiveStocksRestClient {
    client = match query.instrument_type {
        InstrumentType::Equity => client.for_equity(),
        InstrumentType::Option => client.for_options(),
    };
    if let Some(underlying) = &query.underlying {
        client = client.with_option_underlying(underlying.clone());
    }
    client
}

pub(super) fn map_exchange_error(error: ExchangeError) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::Http { status: 429, body } => IntegrationError::RateLimited(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}
