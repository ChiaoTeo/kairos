//! Closed, typed, single-writer runtime for long-running Kairos modules.
//!
//! A Conflux process exposes exactly one owning [`Contract`] and receives one
//! closed ingress type. System composition supplies the complete statically
//! typed client and connection universe; Actors create and use named instances
//! from that universe on demand. The runtime contains no open resource catalog
//! and no erased dispatch path.

mod actor;
mod context;
mod contract;
mod lifecycle;
mod process;
mod resource;
mod system;

pub use actor::ConfluxActor;
pub use context::{CommitContext, Context};
pub use contract::{
    AeronContract, Contract, NoAeron, NoViews, RestCallOf, RestContract, ServedContract,
    ViewContract,
};
pub use lifecycle::{ProcessPhase, ShutdownMode};
pub use process::{
    BuildError, Conflux, ConfluxHandle, ConfluxOutcome, NotifyError, RunError, RuntimeConfig,
};
pub use resource::{
    EnsureDisposition, ManagedClient, ManagedClients, ManagedConnection, ManagedConnections,
    ManagedContract, ResourceError, ResourceState,
};
pub use system::{ConfluxSystem, StaticSystem};
