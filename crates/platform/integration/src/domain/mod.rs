//! Stable Integration facts and connection vocabulary.
//!
//! Participant wire payloads and SDK types must not appear here.

pub mod account;
pub mod connection;
mod decimal;
pub mod earn;
mod event;
pub mod execution;
pub mod instrument;
pub mod market;
pub mod operation;
pub mod participant;
pub mod reference;
pub mod transfer;
pub use account::*;
pub use connection::{
    ConnectionDescriptor, ConnectionDomainRef, ConnectionHealth, ConnectionKey,
    ConnectionLifecycle, ConnectionState, MaintenanceOutcome, ProviderClockHealth,
};
pub use earn::*;
pub use event::{ExternalEventDelivery, ExternalEventEnvelope, ExternalParticipantEvent};
pub use execution::*;
pub use instrument::{ParticipantInstrumentRef, ParticipantInstrumentTypeRef};
pub use market::*;
pub use operation::{
    CommandOutcome, DeliveryCertainty, IndeterminateCommand, ParticipantRejection,
};
pub use participant::{ParticipantKind, ParticipantRef};
pub use reference::*;
pub use transfer::*;
