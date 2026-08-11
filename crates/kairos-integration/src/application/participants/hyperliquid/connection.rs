use crate::application::capabilities::{ParticipantKind, ParticipantRef};
use crate::application::{ConnectionDescriptor, ConnectionDomainRef, IntegrationError};
use crate::services::transport::http::{AsyncPublicHttpClient, ExchangeError, PublicHttpClient};

use super::config::HyperliquidConnectionConfig;
use super::market::{HyperliquidLiveMarket, HyperliquidMarketSnapshot};
use super::reference::{blocking, HyperliquidInstrumentCatalog};

pub struct HyperliquidConnection {
    config: HyperliquidConnectionConfig,
    client: AsyncPublicHttpClient,
}

impl HyperliquidConnection {
    pub fn connect(config: HyperliquidConnectionConfig) -> Result<Self, IntegrationError> {
        if config.environment.trim().is_empty() || config.info_endpoint.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Hyperliquid environment and info endpoint are required".into(),
            ));
        }
        let client = AsyncPublicHttpClient::new("kairos-integration/hyperliquid")
            .map_err(map_exchange_error)?;
        Ok(Self { config, client })
    }

    pub fn instrument_catalog(&self) -> HyperliquidInstrumentCatalog {
        HyperliquidInstrumentCatalog {
            descriptor: self.descriptor(),
            endpoint: self.config.info_endpoint.clone(),
            client: self.client.clone(),
        }
    }

    pub fn market_snapshot(&self) -> HyperliquidMarketSnapshot {
        HyperliquidMarketSnapshot {
            descriptor: self.descriptor(),
            endpoint: self.config.info_endpoint.clone(),
            client: self.client.clone(),
        }
    }

    pub fn live_market(
        &self,
        websocket_url: impl Into<String>,
    ) -> Result<HyperliquidLiveMarket, IntegrationError> {
        HyperliquidLiveMarket::new(self.descriptor(), websocket_url.into())
    }

    pub fn blocking_instrument_catalog(
        &self,
    ) -> Result<blocking::HyperliquidInstrumentCatalog, IntegrationError> {
        Ok(blocking::HyperliquidInstrumentCatalog {
            descriptor: self.descriptor(),
            endpoint: self.config.info_endpoint.clone(),
            client: PublicHttpClient::new("kairos-integration/hyperliquid")
                .map_err(map_exchange_error)?,
        })
    }

    fn descriptor(&self) -> ConnectionDescriptor {
        ConnectionDescriptor {
            binding_id: "hyperliquid.public.market-data".into(),
            participant: ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid")
                .expect("static Hyperliquid participant"),
            environment: self.config.environment.clone(),
            principal_id: None,
            domain: ConnectionDomainRef::new("market-data")
                .expect("static Hyperliquid connection domain"),
        }
    }
}

pub(super) fn map_exchange_error(error: ExchangeError) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::Http { status: 429, body } => IntegrationError::RateLimited(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}
