//! Participant-neutral WebSocket channel mechanics.

mod channel;

pub(crate) use channel::{AsyncSocketEvent, AsyncTokioSocket, SocketEvent, TokioSocket};
