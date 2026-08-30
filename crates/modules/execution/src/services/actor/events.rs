//! Normalized runtime facts consumed by the Execution state owner.

use crate::domain::RemoteOrderUpdate;

/// A normalized order/fill fact produced by an exchange private order stream.
/// This is a fact, not an application command.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RemoteOrderEvent {
    pub event_id: String,
    pub connection_id: String,
    pub event: RemoteOrderUpdate,
}
