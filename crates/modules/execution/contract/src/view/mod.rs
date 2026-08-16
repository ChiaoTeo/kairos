mod active_intents;
mod active_orders;
mod key;
mod metadata;

pub use active_intents::ActiveIntentsView;
pub use active_orders::ActiveOrdersView;
pub use key::{ExecutionViewKey, ExecutionViewKind};
pub use metadata::ViewMetadata;

use crate::{ContractError, ContractResult};
use kairos_transport::SharedSnapshotWriter;
use std::path::Path;

pub struct ViewFrame {
    generation: u64,
    bytes: Vec<u8>,
}
impl ViewFrame {
    pub(crate) fn new(generation: u64, bytes: Vec<u8>) -> Self {
        Self { generation, bytes }
    }
    pub fn generation(&self) -> u64 {
        self.generation
    }
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }
    pub fn active_orders(&self) -> ContractResult<ActiveOrdersView<'_>> {
        active_orders::decode(self.bytes())
    }
    pub fn active_intents(&self) -> ContractResult<ActiveIntentsView<'_>> {
        active_intents::decode(self.bytes())
    }
}

pub struct ExecutionViewReader {
    key: ExecutionViewKey,
    reader: crate::transport::ExecutionMmapReader,
}
impl ExecutionViewReader {
    pub fn open(root: impl AsRef<Path>, key: ExecutionViewKey) -> ContractResult<Self> {
        Ok(Self {
            key: key.clone(),
            reader: crate::transport::ExecutionMmapReader::open(root, key)?,
        })
    }
    pub fn key(&self) -> &ExecutionViewKey {
        &self.key
    }
    pub fn read(&self) -> ContractResult<ViewFrame> {
        self.reader.read()
    }
}

pub struct ExecutionViewPublisher {
    key: ExecutionViewKey,
    writer: SharedSnapshotWriter,
}
impl ExecutionViewPublisher {
    pub fn create(
        root: impl AsRef<Path>,
        key: ExecutionViewKey,
        slot_size: usize,
    ) -> ContractResult<Self> {
        Ok(Self {
            key: key.clone(),
            writer: SharedSnapshotWriter::create(key.resource_path(root), slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }
    pub fn key(&self) -> &ExecutionViewKey {
        &self.key
    }
    pub fn publish(&mut self, generation: u64, payload: &[u8]) -> ContractResult<()> {
        self.writer
            .publish(generation, payload)
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}
