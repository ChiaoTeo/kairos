//! Public typed Reference v2 event capability.

mod decode;
mod frame;
mod stream;

pub use decode::{decode_event, ReferenceEvent};
pub use frame::ReferenceEventFrame;
pub use stream::ReferenceEventStream;
