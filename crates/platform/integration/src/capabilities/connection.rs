//! Transport-independent connection observations.

use std::future::Future;
use std::task::{Context, Poll};
use tokio::time::Instant;

use crate::domain::{ConnectionHealth, MaintenanceOutcome};
use crate::IntegrationError;

/// Bounded observation of a concrete connection's current health.
pub trait ConnectionHealthQuery: Send {
    fn connection_health(&mut self) -> ConnectionHealth;
}

/// Explicit lifecycle control for a stateful concrete connection.
///
/// Bounded REST queries and commands do not need to implement this capability.
pub trait ConnectionLifecycleCommand: Send {
    fn connect(&mut self) -> impl Future<Output = Result<(), IntegrationError>> + Send;
    fn disconnect(&mut self) -> impl Future<Output = Result<(), IntegrationError>> + Send;
    fn reconnect(&mut self) -> impl Future<Output = Result<(), IntegrationError>> + Send;
}

/// Persistent, cancellation-safe maintenance for a stateful connection.
pub trait ConnectionMaintenance: Send {
    fn next_maintenance_at(&self) -> Option<Instant>;

    fn poll_maintenance(
        &mut self,
        cx: &mut Context<'_>,
        now: Instant,
    ) -> Poll<Result<MaintenanceOutcome, IntegrationError>>;
}
