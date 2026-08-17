//! Concrete Account assembly and process-owned contract publishers.

pub mod account;
mod publisher;
pub mod registry;

pub use publisher::{
    empty_snapshot, AeronAccountEventPublisher, FileAccountPublisher, FlatbuffersAccountPublisher,
    MmapAccountPublisher,
};
