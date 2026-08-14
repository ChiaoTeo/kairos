//! Public typed Reference v2 event capability.

#[path = "event/decode.rs"]
mod decode_v2;
#[path = "event/frame.rs"]
mod frame_v2;
#[path = "event/stream.rs"]
mod stream_v2;

pub use decode_v2::{decode_event, ReferenceEvent};
pub use frame_v2::ReferenceEventFrame;
pub use stream_v2::ReferenceEventStream;
