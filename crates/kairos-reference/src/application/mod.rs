//! Public reference use-case boundary.

mod app;
pub mod control;
pub(crate) mod protocol;
mod queries;

pub use app::{ReferenceApplication, ReferenceRefreshResult};
pub use protocol::{CatalogStore, PublishedSnapshots, ReferenceSource};
pub use queries::{MarketQuery, ReferenceKind, ReferenceQuery, ReferenceReader, ReferenceRecord};
