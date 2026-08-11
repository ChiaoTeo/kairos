//! Stable capability contracts and normalized external facts.

pub mod account;
pub mod account_facts;
mod decimal;
pub mod execution;
pub mod execution_facts;
pub mod funding;
pub mod market;
pub mod market_facts;
pub mod reference;

pub use crate::domain::{
    ConnectionDescriptor, ConnectionDomainRef, ConnectionHealth, ConnectionLifecycle,
    ConnectionState, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    ProviderInstrumentRef,
};
pub use account_facts::*;
pub use execution_facts::*;
pub use market_facts::*;
