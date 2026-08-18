use tokio::sync::mpsc;

use super::messages::{SourceCommand, SourceInput};
use crate::domain::source::SourceDescriptor;

pub(crate) struct SourceHandle {
    pub(crate) descriptor: SourceDescriptor,
    pub(crate) commands: mpsc::Sender<SourceCommand>,
    pub(crate) inputs: mpsc::Receiver<SourceInput>,
    pub(crate) task: tokio::task::JoinHandle<()>,
}
