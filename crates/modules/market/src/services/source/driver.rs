use tokio::sync::mpsc;

use super::messages::{SourceCommand, SourceInput};
use crate::domain::market::ResolvedMarket;
use crate::domain::source::SourceDescriptor;

pub(crate) struct SourceHandle {
    pub(crate) descriptor: SourceDescriptor,
    pub(crate) commands: mpsc::Sender<SourceCommand>,
    pub(crate) inputs: mpsc::Receiver<SourceInput>,
    pub(crate) task: tokio::task::JoinHandle<()>,
}

/// Composition-owned demand-driven source construction boundary. Concrete
/// provider selection remains in composition; drivers own only I/O handles.
pub(crate) trait SourceActivator: Send {
    fn activate<'a>(
        &'a mut self,
        market: &'a ResolvedMarket,
        source_input_capacity: usize,
    ) -> std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<SourceHandle, String>> + Send + 'a>,
    >;
}
