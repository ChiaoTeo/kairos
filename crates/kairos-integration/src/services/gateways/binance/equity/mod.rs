//! Binance Equity gateway family.

pub mod client;
pub mod normalizers;
pub mod order;
pub mod query;

mod operations;

use crate::application::reference::{ReferenceCatalogPayload, ReferenceDataConnection};
use crate::application::{Connection, ConnectionSpec};
use crate::domain::{AccessScope, AssetType, IntegrationCapability, ProductFamily, TransportKind};
use crate::services::connections::ManagedConnection;
use crate::services::factories::{GatewaySelector, IntegrationConnectionFactory};

use client::BinanceEquityRestClient;

pub struct BinanceEquityReferenceConnection {
    connection: ManagedConnection,
    operations: operations::BinanceEquityMarketOperations<BinanceEquityRestClient>,
}

#[derive(Clone, Debug)]
pub struct BinanceEquityReferenceFactory {
    api_key: String,
    secret: String,
    base_url: String,
}

impl BinanceEquityReferenceFactory {
    pub fn new(api_key: impl Into<String>, secret: impl Into<String>) -> Self {
        Self {
            api_key: api_key.into(),
            secret: secret.into(),
            base_url: "https://api.binance.com".into(),
        }
    }

    pub fn open(&self) -> Result<BinanceEquityReferenceConnection, String> {
        let client = BinanceEquityRestClient::with_base_url(
            self.api_key.clone(),
            self.secret.clone(),
            self.base_url.clone(),
        )
        .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: "reference.binance.equity.rest".into(),
            provider: "binance".into(),
            product: Some(ProductFamily::Equity),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Reference,
            credential_id: None,
            asset_type: Some(AssetType::Equity),
        };
        let connection = ManagedConnection::new(spec, Vec::new())?;
        Ok(BinanceEquityReferenceConnection {
            connection,
            operations: operations::BinanceEquityMarketOperations::new(client),
        })
    }
}

impl Connection for BinanceEquityReferenceConnection {
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

impl ReferenceDataConnection for BinanceEquityReferenceConnection {
    fn fetch_reference_catalog(&mut self) -> Result<ReferenceCatalogPayload, String> {
        self.start()?;
        let payload = self.operations.exchange_info()?;
        normalizers::catalog(&payload)
    }
}

impl BinanceEquityReferenceFactory {
    pub fn open_connection(&self) -> Result<Box<dyn ReferenceDataConnection>, String> {
        Ok(Box::new(BinanceEquityReferenceFactory::open(self)?))
    }
}

impl IntegrationConnectionFactory for BinanceEquityReferenceFactory {
    fn connect(&self, spec: &ConnectionSpec) -> Result<Box<dyn Connection>, String> {
        if !self.supports(spec) {
            return Err("Binance Equity factory does not support this connection spec".into());
        }
        Ok(Box::new(BinanceEquityReferenceFactory::open(self)?))
    }
}

impl crate::services::factories::GatewaySelector for BinanceEquityReferenceFactory {
    fn supports(&self, spec: &ConnectionSpec) -> bool {
        spec.provider == "binance"
            && spec.product == Some(ProductFamily::Equity)
            && spec.access == AccessScope::Public
            && spec.transport == TransportKind::Rest
            && spec.capability == IntegrationCapability::Reference
    }
}
