use tokio::sync::mpsc;

use super::messages::{SourceCommand, SourceInput};
use crate::domain::source::FeedDescriptor;

pub(crate) struct SourceHandle {
    pub(crate) descriptor: FeedDescriptor,
    pub(crate) commands: mpsc::Sender<SourceCommand>,
    pub(crate) inputs: mpsc::Receiver<SourceInput>,
    pub(crate) task: tokio::task::JoinHandle<()>,
}
