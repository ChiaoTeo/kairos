//! Contract-owned read-only access to the durable Reference catalog.

pub mod model;
pub mod sqlite;

pub use model::*;
pub use sqlite::*;
