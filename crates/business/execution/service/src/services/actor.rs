//! Runtime input facts for the Execution state owner.
//!
//! Commands and venue facts are deliberately different inputs. Commands are
//! requests from callers and have a reply path; venue events are facts from a
//! private order stream and are consumed independently by the state owner.

use crate::application::VenueOrderUpdate;

/// A normalized order/fill fact produced by the venue's private order stream.
///
/// This is not an application command and must never be mixed with Market,
/// Reference, Account, or Risk events.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct VenueOrderEvent {
    pub event_id: String,
    pub connection_id: String,
    pub event: VenueOrderUpdate,
}
