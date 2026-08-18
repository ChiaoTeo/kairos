//! Transport-independent connection observations.

use std::future::Future;

use crate::domain::ConnectionHealth;
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
