//! Public cross-process contract for the Reference module.
//!
//! This crate owns the stable SQLite/event boundary and its transport
//! adapters. It deliberately does not depend on the Reference service's
//! domain, actor, persistence, or provider implementation.

pub mod encoding;
pub mod error;
pub mod event;
pub mod model;
pub mod projection;
pub mod sqlite;
pub mod transport;

pub use error::{ContractError, ContractResult};
pub use event::{decode_change, EventEnvelope, EventPublisher, ReferenceChange};
pub use model::{LifecycleEvent, ReferenceCatalog};
pub use projection::{ReferenceHealth, ReferenceMarket};
pub use sqlite::{
    ReferenceCatalogStats, ReferenceCollection, ReferenceMarketPage, ReferenceProjection,
    ReferenceSqliteReader, ReferenceWatermark, SqliteExecutionAccessQuery,
    SqliteInstrumentQuery, SqliteMarketDataAccessQuery, SqliteMarketQuery,
    REFERENCE_SQLITE_SCHEMA_VERSION,
};
