//! Stable identity and health for one participant-owned connection domain.

use kairos_primitives::UnixNanos;

use super::ParticipantRef;

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ConnectionDomainRef {
    code: String,
}

impl ConnectionDomainRef {
    pub fn new(code: impl Into<String>) -> Result<Self, String> {
        let code = code.into();
        if code.trim().is_empty() {
            return Err("connection domain code is required".into());
        }
        Ok(Self { code })
    }

    pub fn as_str(&self) -> &str {
        &self.code
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConnectionDescriptor {
    pub binding_id: String,
    pub participant: ParticipantRef,
    pub environment: String,
    pub principal_id: Option<String>,
    pub domain: ConnectionDomainRef,
}

impl ConnectionDescriptor {
    pub fn new(
        binding_id: impl Into<String>,
        participant: ParticipantRef,
        domain: impl Into<String>,
    ) -> Result<Self, String> {
        let value = Self {
            binding_id: binding_id.into(),
            participant,
            environment: "unspecified".into(),
            principal_id: None,
            domain: ConnectionDomainRef::new(domain)?,
        };
        value.validate()?;
        Ok(value)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.binding_id.trim().is_empty() {
            return Err("connection binding id is required".into());
        }
        if self.environment.trim().is_empty() {
            return Err("connection environment is required".into());
        }
        if self
            .principal_id
            .as_ref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err("connection principal id cannot be empty".into());
        }
        Ok(())
    }
}

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
pub struct ConnectionState {
    pub identity: ConnectionDescriptor,
    pub lifecycle: ConnectionLifecycle,
    pub authenticated: bool,
    pub connected_at_unix_nanos: Option<UnixNanos>,
    pub last_error: Option<String>,
    pub reconnect_count: u64,
}

impl ConnectionState {
    pub fn new(identity: ConnectionDescriptor) -> Self {
        Self {
            identity,
            lifecycle: ConnectionLifecycle::Created,
            authenticated: false,
            connected_at_unix_nanos: None,
            last_error: None,
            reconnect_count: 0,
        }
    }

    pub fn mark_ready(&mut self, authenticated: bool) {
        self.lifecycle = ConnectionLifecycle::Ready;
        self.authenticated = authenticated;
        self.last_error = None;
    }

    pub fn mark_reconnected(&mut self, authenticated: bool) {
        self.mark_ready(authenticated);
        self.reconnect_count = self.reconnect_count.saturating_add(1);
    }

    pub fn mark_stopped(&mut self) {
        self.lifecycle = ConnectionLifecycle::Stopped;
        self.authenticated = false;
    }

    pub fn mark_failed(&mut self, error: impl Into<String>) {
        self.lifecycle = ConnectionLifecycle::Failed;
        self.authenticated = false;
        self.last_error = Some(error.into());
    }

    pub fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: matches!(
                self.lifecycle,
                ConnectionLifecycle::Ready | ConnectionLifecycle::Degraded
            ),
            authenticated: self.authenticated,
            last_error: self.last_error.clone(),
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
