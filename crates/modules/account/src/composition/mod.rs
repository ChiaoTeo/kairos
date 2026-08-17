//! Concrete Account assembly and process-owned contract publishers.

pub mod account;
pub mod registry;
mod publisher;

pub use publisher::{
    empty_snapshot, AeronAccountEventPublisher, FileAccountPublisher, FlatbuffersAccountPublisher,
    MmapAccountPublisher,
};
