//! Process-boundary transport adapters.

pub mod aeron;
pub mod mutable_memory;
pub mod shared_memory;

pub use aeron::{AeronBytePublisher, AeronByteSubscription};
pub use mutable_memory::{MutableFlatbufferReader, MutableFlatbufferWriter, MutableSnapshot};
pub use shared_memory::{
    SharedSnapshotPayload, SharedSnapshotReader, SharedSnapshotWriter, SnapshotMarketData,
};
