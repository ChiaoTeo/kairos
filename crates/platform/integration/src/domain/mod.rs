//! Stable connection vocabulary. Provider SDK types must not appear here.

pub mod connection;
pub mod instrument;
pub mod operation;
pub mod participant;
pub use connection::{
    ConnectionDescriptor, ConnectionDomainRef, ConnectionHealth, ConnectionLifecycle,
    ConnectionState,
};
pub use instrument::{ParticipantInstrumentTypeRef, ProviderInstrumentRef};
pub use operation::{CommandOutcome, DeliveryCertainty, IndeterminateCommand, ProviderRejection};
pub use participant::{ParticipantKind, ParticipantRef};
