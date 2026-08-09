//! Public cross-process contract for the Reference module.
//!
//! This crate owns the stable snapshot/event envelopes and their transport
//! adapters. It deliberately does not depend on the Reference service's
//! domain, actor, persistence, or provider implementation.

pub mod encoding;
pub mod error;
pub mod event;
pub mod model;
pub mod query;
pub mod snapshot;
pub mod transport;

pub use error::{ContractError, ContractResult};
pub use event::{EventEnvelope, EventPublisher};
pub use model::{LifecycleEvent, ReferenceCatalog};
pub use snapshot::{SnapshotEnvelope, SnapshotPublisher, SnapshotReader};
