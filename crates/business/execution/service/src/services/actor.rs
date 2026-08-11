//! Runtime input facts for the Execution state owner.
//!
//! Commands and exchange facts are deliberately different inputs. Commands are
//! requests from callers and have a reply path; exchange events are facts from a
//! private order stream and are consumed independently by the state owner.

use crate::application::RemoteOrderUpdate;

/// A normalized order/fill fact produced by the exchange's private order stream.
///
/// This is not an application command and must never be mixed with Market,
/// Reference, Account, or Risk events.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RemoteOrderEvent {
    pub event_id: String,
    pub connection_id: String,
    pub event: RemoteOrderUpdate,
}
