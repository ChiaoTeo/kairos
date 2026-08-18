//! Transport configuration shared by concrete Binance product connections.

use crate::domain::{ConnectionDescriptor, ParticipantKind, ParticipantRef};
use crate::IntegrationError;
use secrecy::SecretString;

#[derive(Clone)]
pub struct BinanceCredential {
    pub principal_id: String,
    pub api_key: SecretString,
    pub secret: SecretString,
}

impl std::fmt::Debug for BinanceCredential {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("BinanceCredential")
            .field("principal_id", &self.principal_id)
            .field("api_key", &"[REDACTED]")
            .field("secret", &"[REDACTED]")
            .finish()
    }
}

#[derive(Clone, Debug)]
pub struct BinanceRestConfig {
    pub environment: String,
    pub endpoint: String,
    pub credential: Option<BinanceCredential>,
}

impl BinanceRestConfig {
    pub fn descriptor(
        &self,
        connection_key: crate::ConnectionKey,
        domain: &str,
    ) -> Result<ConnectionDescriptor, IntegrationError> {
        descriptor(
            connection_key,
            &self.environment,
            self.credential
                .as_ref()
                .map(|value| value.principal_id.clone()),
            domain,
        )
    }
}

#[derive(Clone, Debug)]
pub struct BinanceWebSocketConfig {
    pub environment: String,
    pub endpoint: String,
    pub credential: Option<BinanceCredential>,
    pub event_capacity: usize,
}

#[derive(Clone, Debug)]
pub struct BinanceUserWebSocketConfig {
    pub environment: String,
    pub rest_endpoint: String,
    pub websocket_endpoint: String,
    pub credential: BinanceCredential,
    pub event_capacity: usize,
    pub segment_key: String,
}

impl BinanceUserWebSocketConfig {
    pub fn descriptor(
        &self,
        connection_key: crate::ConnectionKey,
        domain: &str,
    ) -> Result<ConnectionDescriptor, IntegrationError> {
        descriptor(
            connection_key,
            &self.environment,
            Some(self.credential.principal_id.clone()),
            domain,
        )
    }
}

impl BinanceWebSocketConfig {
    pub fn descriptor(
        &self,
        connection_key: crate::ConnectionKey,
        domain: &str,
    ) -> Result<ConnectionDescriptor, IntegrationError> {
        descriptor(
            connection_key,
            &self.environment,
            self.credential
                .as_ref()
                .map(|value| value.principal_id.clone()),
            domain,
        )
    }
}

fn descriptor(
    connection_key: crate::ConnectionKey,
    environment: &str,
    principal_id: Option<String>,
    domain: &str,
) -> Result<ConnectionDescriptor, IntegrationError> {
    let mut descriptor = ConnectionDescriptor::new(
        connection_key,
        ParticipantRef::new(ParticipantKind::Exchange, "binance")
            .map_err(IntegrationError::InvalidRequest)?,
        domain,
    )
    .map_err(IntegrationError::InvalidRequest)?;
    descriptor.environment = environment.into();
    descriptor.principal_id = principal_id;
    descriptor
        .validate()
        .map_err(IntegrationError::InvalidRequest)?;
    Ok(descriptor)
}
