use std::path::Path;

use kairos_transport::{SharedSnapshotReader, SharedSnapshotWriter};

use crate::{ContractError, ContractResult, MarketViewKey, ViewFrame};

pub struct MarketMmapReader {
    key: MarketViewKey,
    reader: SharedSnapshotReader,
}

impl MarketMmapReader {
    pub fn open(root: impl AsRef<Path>, key: MarketViewKey) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(key.resource_path(root))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, reader })
    }

    pub fn key(&self) -> &MarketViewKey {
        &self.key
    }

    pub fn read(&self) -> ContractResult<ViewFrame> {
        let payload = self
            .reader
            .read_payload()
            .map_err(ContractError::Transport)?;
        Ok(ViewFrame::new(payload.generation, payload.payload))
    }
}

pub struct MarketMmapWriter {
    key: MarketViewKey,
    writer: SharedSnapshotWriter,
}

impl MarketMmapWriter {
    pub fn create(
        root: impl AsRef<Path>,
        key: MarketViewKey,
        slot_size: usize,
    ) -> ContractResult<Self> {
        let writer = SharedSnapshotWriter::create(key.resource_path(root), slot_size)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, writer })
    }

    pub fn key(&self) -> &MarketViewKey {
        &self.key
    }

    pub fn publish(&mut self, generation: u64, payload: &[u8]) -> ContractResult<()> {
        self.writer
            .publish(generation, payload)
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}
