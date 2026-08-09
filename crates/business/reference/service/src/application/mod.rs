//! Public reference use-case boundary.

mod app;
pub mod control;
mod queries;

#[cfg(test)]
mod tests;

pub use app::{ReferenceApplication, ReferenceReadModel, ReferenceRefreshResult};
pub use queries::{LifecycleQuery, MarketQuery, ReferenceKind, ReferenceQuery, ReferenceRecord};
