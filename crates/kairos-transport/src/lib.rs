//! Process-boundary transport adapters.

pub mod shared_memory;

pub use shared_memory::{SharedSnapshotReader, SharedSnapshotWriter, SnapshotMarketData};
