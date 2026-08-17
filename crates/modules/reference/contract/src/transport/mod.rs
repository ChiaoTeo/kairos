//! Concrete transport adapters for the Reference event contract.

pub mod model;
pub mod projection;
pub mod sqlite;

pub use model::*;
pub use projection::{ReferenceHealth, ReferenceMarket};
pub use sqlite::*;
