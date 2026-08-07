//! Public Market use cases and consumer-owned protocols.

pub mod protocol;
mod service;
pub mod wire;

pub use crate::services::actor::MarketSnapshot;
pub use service::{MarketApplication, MarketError};
