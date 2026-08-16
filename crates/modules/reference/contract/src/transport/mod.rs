//! Concrete transport adapters for the Reference event contract.

mod aeron;
pub mod model;
pub mod projection;
pub mod sqlite;

pub use aeron::ReferenceAeronTransport;
pub use model::*;
pub use projection::{ReferenceHealth, ReferenceMarket};
pub use sqlite::*;
