//! Integration I/O drivers for the single Market Actor.
//!
//! Drivers own provider capability handles and protocol I/O. They communicate
//! exclusively through private Market messages and never own Market business
//! state.

mod driver;
pub(crate) mod messages;
mod normalization;
mod recovery;
mod replay;
mod snapshot;
mod stream;

pub(crate) use driver::{SourceActivator, SourceHandle};
pub(crate) use replay::{load_replay_checkpoint, spawn_replay, ReplayClock, ReplaySource};
pub(crate) use snapshot::spawn_snapshot;
pub(crate) use stream::{spawn_stream, spawn_stream_with_policy, StreamFailurePolicy};
