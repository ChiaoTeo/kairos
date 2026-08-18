//! Participant-neutral WebSocket channel mechanics.

mod channel;
mod dispatcher;

pub(crate) use channel::{SocketEvent, TokioSocket};
pub(crate) use dispatcher::InboundDispatcher;
