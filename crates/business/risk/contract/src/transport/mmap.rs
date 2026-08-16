use crate::view::{RiskViewKey, ViewFrame};
use crate::{ContractError, ContractResult};
use kairos_transport::{SharedSnapshotReader, SharedSnapshotWriter};
use std::path::Path;
pub struct RiskMmapReader {
    key: RiskViewKey,
    reader: SharedSnapshotReader,
}
impl RiskMmapReader {
    pub fn open(root: impl AsRef<Path>, key: RiskViewKey) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(key.resource_path(root))
            .map_err(|e| ContractError::Transport(e.to_string()))?;
        Ok(Self { key, reader })
    }
    pub fn read(&self) -> ContractResult<ViewFrame> {
        let p = self
            .reader
            .read_payload()
            .map_err(ContractError::Transport)?;
        Ok(ViewFrame::new(p.generation, p.payload))
    }
    pub fn key(&self) -> &RiskViewKey {
        &self.key
    }
}
pub struct RiskMmapWriter {
    key: RiskViewKey,
    writer: SharedSnapshotWriter,
}
impl RiskMmapWriter {
    pub fn create(
        root: impl AsRef<Path>,
        key: RiskViewKey,
        slot_size: usize,
    ) -> ContractResult<Self> {
        let writer = SharedSnapshotWriter::create(key.resource_path(root), slot_size)
            .map_err(|e| ContractError::Transport(e.to_string()))?;
        Ok(Self { key, writer })
    }
    pub fn publish(&mut self, generation: u64, payload: &[u8]) -> ContractResult<()> {
        self.writer
            .publish(generation, payload)
            .map_err(|e| ContractError::Transport(e.to_string()))
    }
    pub fn key(&self) -> &RiskViewKey {
        &self.key
    }
}
