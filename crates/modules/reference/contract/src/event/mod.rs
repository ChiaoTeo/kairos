//! Public typed Reference v2 event capability.

mod decode;
mod frame;
mod publisher;
mod stream;

pub use decode::{
    ReferenceEvent, ReferenceEventKind, ReferenceEventView, decode_event,
    decode_event as decode_event_view,
};
pub use frame::ReferenceEventFrame;
pub use publisher::ReferenceEventPublisher;
pub use stream::ReferenceEventStream;
