//! Concrete transport adapters for the Reference contract.

mod aeron;
mod reference_aeron;

pub use aeron::{AeronEventPublisher, AeronEventSubscriber};
pub use reference_aeron::ReferenceAeronEventWriter;
