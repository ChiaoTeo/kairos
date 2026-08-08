use super::{AccessScope, IntegrationCapability, IntegrationRoute, ProductFamily, TransportKind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectionLifecycle {
    Created,
    Starting,
    Ready,
    Degraded,
    Stopping,
    Stopped,
    Failed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConnectionIdentity {
    pub connection_id: String,
    pub route: IntegrationRoute,
    pub product: Option<ProductFamily>,
    pub access: AccessScope,
    pub transport: TransportKind,
    pub capability: IntegrationCapability,
}

impl ConnectionIdentity {
    pub fn new(
        connection_id: impl Into<String>,
        route: IntegrationRoute,
        product: Option<ProductFamily>,
        access: AccessScope,
        transport: TransportKind,
        capability: IntegrationCapability,
    ) -> Result<Self, String> {
        let connection_id = connection_id.into();
        if connection_id.trim().is_empty() {
            return Err("connection id is required".into());
        }
        route.validate()?;
        Ok(Self {
            connection_id,
            route,
            product,
            access,
            transport,
            capability,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConnectionState {
    pub identity: ConnectionIdentity,
    pub lifecycle: ConnectionLifecycle,
    pub authenticated: bool,
    pub connected_at_unix_nanos: Option<u64>,
    pub last_error: Option<String>,
    pub reconnect_count: u64,
}

impl ConnectionState {
    pub fn new(identity: ConnectionIdentity) -> Self {
        Self {
            identity,
            lifecycle: ConnectionLifecycle::Created,
            authenticated: false,
            connected_at_unix_nanos: None,
            last_error: None,
            reconnect_count: 0,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConnectionHealth {
    pub lifecycle: ConnectionLifecycle,
    pub healthy: bool,
    pub authenticated: bool,
    pub last_error: Option<String>,
}
