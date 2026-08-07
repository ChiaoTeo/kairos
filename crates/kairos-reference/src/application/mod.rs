//! Public reference use-case boundary.

mod app;
pub mod control;
pub mod protocol;
mod queries;
pub mod runtime;

pub use app::{ReferenceApplication, ReferenceRefreshResult};
pub use queries::{MarketQuery, ReferenceKind, ReferenceQuery, ReferenceReader, ReferenceRecord};
