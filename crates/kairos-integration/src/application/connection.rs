//! Stable lifecycle protocol for connections returned by integration.
//!
//! This is deliberately a small protocol.  It describes how a caller owns a
//! connection, but it does not describe provider operations.  Market streams,
//! account streams and request APIs extend this protocol independently.

use crate::domain::{ConnectionHealth, ConnectionIdentity, ConnectionState};
use crate::domain::{OrderEntryEvent, OrderEntryRequest};
pub trait Connection: Send {
    fn identity(&self) -> &ConnectionIdentity;
    fn state(&self) -> &ConnectionState;
    fn start(&mut self) -> Result<(), String>;
    fn stop(&mut self) -> Result<(), String>;
    fn reconnect(&mut self) -> Result<(), String>;
    fn health(&self) -> ConnectionHealth;
}

/// Order-entry capability for a provider connection.
///
/// This is deliberately an extension of the lifecycle connection rather than
/// part of `Connection` itself: reference and market connections do not gain
/// order semantics merely because they share lifecycle management.
pub trait OrderEntryConnection: Connection {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String>;
    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        venue_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<OrderEntryEvent, String>;
}
