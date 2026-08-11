//! Integration I/O drivers for the single Market Actor.
//!
//! Drivers own provider capability handles and protocol I/O. They communicate
//! exclusively through private Market messages and never own Market business
//! state.

mod binance;
mod replay;
mod snapshot;
mod stream;

use tokio::sync::mpsc;

use crate::domain::market::MarketDescriptor;
use crate::domain::source::SourceDescriptor;
use crate::services::messages::SourceCommand;

pub(crate) use binance::spawn_binance;
pub(crate) use replay::{load_actor_checkpoint, spawn_replay, ReplaySource};
pub(crate) use snapshot::spawn_snapshot;
pub(crate) use stream::spawn_stream;

pub(crate) struct SourceHandle {
    pub(crate) descriptor: SourceDescriptor,
    pub(crate) commands: mpsc::Sender<SourceCommand>,
    pub(crate) inputs: mpsc::Receiver<crate::services::messages::SourceInput>,
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
        market: &'a MarketDescriptor,
        source_input_capacity: usize,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<SourceHandle, String>> + Send + 'a>,
    >;
}
