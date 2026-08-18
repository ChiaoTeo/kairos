//! Integration I/O drivers for the single Market Actor.
//!
//! Drivers own provider capability handles and protocol I/O. They communicate
//! exclusively through private Market messages and never own Market business
//! state.

mod driver;
pub(crate) mod messages;
mod normalization;
mod replay;
mod snapshot;
mod stream;

pub(crate) use driver::SourceHandle;
pub(crate) use normalization::{normalize, with_epoch};
pub(crate) use replay::{load_replay_checkpoint, spawn_replay, ReplayClock, ReplaySource};
pub(crate) use snapshot::quote_event;
pub(crate) use stream::{confirmed_subscription, confirmed_unsubscription, subscription_request};
