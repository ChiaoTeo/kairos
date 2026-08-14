use crate::{ContractError, ContractResult, ExecutionViewKey, ViewFrame};
use kairos_transport::{SharedSnapshotReader, SharedSnapshotWriter};
use std::path::Path;
pub struct ExecutionMmapReader {
    key: ExecutionViewKey,
    reader: SharedSnapshotReader,
}
impl ExecutionMmapReader {
    pub fn open(root: impl AsRef<Path>, key: ExecutionViewKey) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(key.resource_path(root))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, reader })
    }
    pub fn key(&self) -> &ExecutionViewKey {
        &self.key
    }
    pub fn read(&self) -> ContractResult<ViewFrame> {
        let payload = self
            .reader
            .read_payload()
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(ViewFrame::new(payload.generation, payload.payload))
    }
}
pub struct ExecutionMmapWriter {
    key: ExecutionViewKey,
    writer: SharedSnapshotWriter,
}
impl ExecutionMmapWriter {
    pub fn create(
        root: impl AsRef<Path>,
        key: ExecutionViewKey,
        slot_size: usize,
    ) -> ContractResult<Self> {
        let writer = SharedSnapshotWriter::create(key.resource_path(root), slot_size)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, writer })
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
