//! Shared FlatBuffers metadata helpers. Business event and view encoders live
//! under their owning capability (`event/` and `view/`).

mod risk;

pub use risk::{FlatbuffersRiskEventWriter, RiskAeronEventPublisher, encode_indexed_current};
