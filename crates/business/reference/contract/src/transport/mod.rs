//! Concrete transport adapters for the Reference contract.

mod aeron;
mod mmap;
mod reference_aeron;
mod reference_mmap;

pub use aeron::{AeronEventPublisher, AeronEventSubscriber};
pub use mmap::{MmapSnapshotPublisher, MmapSnapshotReader};
pub use reference_aeron::ReferenceAeronEventWriter;
pub use reference_mmap::ReferenceMmapSnapshotWriter;
