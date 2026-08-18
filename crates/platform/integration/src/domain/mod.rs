//! Stable Integration facts and connection vocabulary.
//!
//! Participant wire payloads and SDK types must not appear here.

pub mod account;
pub mod connection;
mod decimal;
mod event;
pub mod execution;
pub mod funding;
pub mod instrument;
pub mod market;
pub mod operation;
pub mod participant;
pub mod reference;
pub use account::*;
pub use connection::{
    ConnectionDescriptor, ConnectionDomainRef, ConnectionHealth, ConnectionLifecycle,
    ConnectionState,
};
pub use event::{ExternalEventEnvelope, ExternalParticipantEvent};
pub use execution::*;
pub use funding::*;
pub use instrument::{ParticipantInstrumentRef, ParticipantInstrumentTypeRef};
pub use market::*;
pub use operation::{
    CommandOutcome, DeliveryCertainty, IndeterminateCommand, ParticipantRejection,
};
pub use participant::{ParticipantKind, ParticipantRef};
pub use reference::*;
