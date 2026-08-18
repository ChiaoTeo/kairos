//! Closed, typed, single-writer runtime for long-running Kairos modules.
//!
//! A Conflux process exposes exactly one owning [`Contract`] and receives every
//! source through one closed [`ConfluxEvent`] enum. [`ConfluxSystem`] contains
//! the complete concrete client and connection universe. The runtime contains
//! no open resource catalog and no erased dispatch path.

mod actor;
mod context;
mod contract;
mod event;
mod lifecycle;
mod process;
mod resource;
mod system;

pub use actor::ConfluxActor;
pub use context::Context;
pub use contract::{Contract, RestContract, RestRequestOf, RestResponseOf};
pub use event::{ConfluxEvent, ContractEvent, IntegrationEvent, SystemEvent};
pub use lifecycle::{ProcessPhase, ShutdownMode};
pub use process::{
    BuildError, Conflux, ConfluxConfig, ConfluxHandle, ConfluxOutcome, HandleError, RunError,
};
pub use resource::{
    EnsureDisposition, ManagedClient, ManagedClients, ManagedConnection, ManagedConnections,
    ManagedResource, NamedResources, ResourceError, ResourceState,
};
pub use system::ConfluxSystem;
