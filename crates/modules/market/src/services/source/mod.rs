//! Integration I/O drivers for the single Market Actor.
//!
//! Drivers own provider capability handles and protocol I/O. They communicate
//! exclusively through private Market messages and never own Market business
//! state.

pub(crate) mod messages;
mod replay;
mod snapshot;
mod stream;

use tokio::sync::mpsc;

use self::messages::SourceCommand;
use crate::domain::market::ResolvedMarket;
use crate::domain::source::SourceDescriptor;

pub(crate) use replay::{load_replay_checkpoint, spawn_replay, ReplayClock, ReplaySource};
pub(crate) use snapshot::spawn_snapshot;
pub(crate) use stream::{spawn_stream, spawn_stream_with_policy, StreamFailurePolicy};

pub(crate) struct SourceHandle {
    pub(crate) descriptor: SourceDescriptor,
    pub(crate) commands: mpsc::Sender<SourceCommand>,
    pub(crate) inputs: mpsc::Receiver<messages::SourceInput>,
    pub(crate) task: tokio::task::JoinHandle<()>,
}

/// Composition-owned, demand-driven source construction boundary.
///
/// The actor asks for a source only after a subscription has resolved to a
/// route. Implementations may select provider-native capabilities, but they
/// must not own Market subscriptions or source lifecycle state.
pub(crate) trait SourceActivator: Send {
    fn activate<'a>(
        &'a mut self,
        market: &'a ResolvedMarket,
        source_input_capacity: usize,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<SourceHandle, String>> + Send + 'a>,
    >;
}
