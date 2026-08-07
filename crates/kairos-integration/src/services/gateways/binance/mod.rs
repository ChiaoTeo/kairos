//! Binance endpoint vocabulary and provider connections without business entities.

pub(crate) mod earn;
pub(crate) mod equity;
pub(crate) mod funding;
pub(crate) mod futures;
pub(crate) mod margin;
pub(crate) mod margin_order;
pub(crate) mod options;
pub(crate) mod order_query;
pub(crate) mod spot;
pub(crate) mod transfer;

use serde_json::Value;

use crate::application::reference::{ReferenceCatalogPayload, ReferenceDataConnection};
use crate::application::{Connection, ConnectionSpec};
use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::connections::ManagedConnection;
use crate::services::drivers::http::ExchangeError;
use crate::services::factories::{GatewaySelector, IntegrationConnectionFactory};
use crate::services::gateways::binance::spot::client::SpotRestClient;
use crate::services::gateways::binance::spot::public_rest::SpotPublicRest;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Market {
    Spot,
    Options,
}

/// Public REST connection used by Reference and other read-only applications.
///
/// The request client, endpoint and lifecycle state are intentionally hidden
/// behind this connection. Callers only receive the provider payload at the
/// adapter boundary and must normalize it in their own business module.
pub struct BinanceReferenceConnection {
    connection: ManagedConnection,
    client: SpotRestClient,
    market: Market,
}

/// Composition root for Binance reference connections.
///
/// The consuming business module receives the capability trait; it does not
/// construct this provider connection or know its HTTP implementation.
#[derive(Clone, Debug)]
pub struct BinanceReferenceFactory {
    endpoint: String,
    market: Market,
}

impl BinanceReferenceFactory {
    pub fn new(endpoint: impl Into<String>) -> Result<Self, ExchangeError> {
        Self::for_market(endpoint, Market::Spot)
    }

    pub fn options(endpoint: impl Into<String>) -> Result<Self, ExchangeError> {
        Self::for_market(endpoint, Market::Options)
    }

    pub fn for_market(endpoint: impl Into<String>, market: Market) -> Result<Self, ExchangeError> {
        let endpoint = endpoint.into();
        if endpoint.trim().is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Binance endpoint is required".into(),
            ));
        }
        Ok(Self { endpoint, market })
    }
}

impl BinanceReferenceConnection {
    pub fn open_for_market(
        endpoint: impl Into<String>,
        market: Market,
    ) -> Result<Self, ExchangeError> {
        let endpoint = endpoint.into();
        if endpoint.trim().is_empty() {
            return Err(ExchangeError::InvalidRequest(
                "Binance endpoint is required".into(),
            ));
        }
        let spec = ConnectionSpec {
            connection_id: format!("reference.binance.{}.rest", market_name(market)),
            provider: "binance".into(),
            product: Some(product_family(market)),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Reference,
            credential_id: None,
            asset_type: None,
        };
        let connection =
            ManagedConnection::new(spec, Vec::new()).map_err(ExchangeError::InvalidRequest)?;
        let client = SpotRestClient::new(endpoint.clone())?;
        Ok(Self {
            connection,
            client,
            market,
        })
    }

    pub fn fetch_exchange_info(&mut self) -> Result<Value, ExchangeError> {
        self.connection.start().map_err(ExchangeError::Connection)?;
        self.client.fetch_exchange_info()
    }
}

impl Connection for BinanceReferenceConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }

    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }

    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }

    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }

    fn reconnect(&mut self) -> Result<(), String> {
        self.connection.reconnect()
    }

    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}

impl ReferenceDataConnection for BinanceReferenceConnection {
    fn fetch_reference_catalog(&mut self) -> Result<ReferenceCatalogPayload, String> {
        let payload = self
            .fetch_exchange_info()
            .map_err(|error| error.to_string())?;
        match self.market {
            Market::Spot => spot::normalizers::catalog(&payload),
            Market::Options => options::normalizers::catalog(&payload),
        }
    }
}

impl BinanceReferenceFactory {
    pub fn open(&self) -> Result<Box<dyn ReferenceDataConnection>, String> {
        Ok(Box::new(
            BinanceReferenceConnection::open_for_market(self.endpoint.clone(), self.market)
                .map_err(|error| error.to_string())?,
        ))
    }
}

impl IntegrationConnectionFactory for BinanceReferenceFactory {
    fn connect(&self, spec: &ConnectionSpec) -> Result<Box<dyn Connection>, String> {
        if spec.provider != "binance"
            || spec.capability != IntegrationCapability::Reference
            || spec.access != AccessScope::Public
            || spec.product != Some(product_family(self.market))
        {
            return Err("Binance reference factory does not support this connection spec".into());
        }
        Ok(Box::new(
            BinanceReferenceConnection::open_for_market(self.endpoint.clone(), self.market)
                .map_err(|error| error.to_string())?,
        ))
    }
}

impl GatewaySelector for BinanceReferenceFactory {
    fn supports(&self, spec: &ConnectionSpec) -> bool {
        spec.provider == "binance"
            && spec.capability == IntegrationCapability::Reference
            && spec.access == AccessScope::Public
            && spec.product == Some(product_family(self.market))
    }
}

const fn product_family(market: Market) -> ProductFamily {
    match market {
        Market::Options => ProductFamily::Options,
        _ => ProductFamily::Spot,
    }
}

const fn market_name(market: Market) -> &'static str {
    match market {
        Market::Spot => "spot",
        Market::Options => "options",
    }
}

#[cfg(test)]
mod tests {
    use super::{BinanceReferenceConnection, BinanceReferenceFactory, Market};
    use crate::application::Connection;
    use crate::services::factories::GatewaySelector;

    #[test]
    fn reference_connection_owns_provider_lifecycle() {
        let mut connection = BinanceReferenceConnection::open_for_market(
            "https://api.binance.com/api/v3/exchangeInfo",
            Market::Spot,
        )
        .unwrap();
        assert_eq!(
            connection.identity().capability,
            crate::domain::IntegrationCapability::Reference
        );
        connection.start().unwrap();
        assert!(connection.health().healthy);
        connection.stop().unwrap();
    }

    #[test]
    fn factory_returns_reference_capability_without_exposing_provider_client() {
        let factory =
            BinanceReferenceFactory::new("https://api.binance.com/api/v3/exchangeInfo").unwrap();
        let connection = factory.open().unwrap();
        assert_eq!(connection.identity().provider, "binance");
        assert_eq!(
            connection.identity().capability,
            crate::domain::IntegrationCapability::Reference
        );
    }

    #[test]
    fn options_factory_selects_options_identity() {
        let factory =
            BinanceReferenceFactory::options("https://eapi.binance.com/eapi/v1/exchangeInfo")
                .unwrap();
        let connection = factory.open().unwrap();
        assert_eq!(
            connection.identity().product,
            Some(crate::domain::ProductFamily::Options)
        );
        assert!(factory.supports(&crate::application::ConnectionSpec {
            connection_id: "reference.binance.options.rest".into(),
            provider: "binance".into(),
            product: Some(crate::domain::ProductFamily::Options),
            access: crate::domain::AccessScope::Public,
            transport: crate::domain::TransportKind::Rest,
            capability: crate::domain::IntegrationCapability::Reference,
            credential_id: None,
            asset_type: None,
        }));
    }
}
