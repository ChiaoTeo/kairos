use std::path::Path;

use kairos_transport::{SharedSnapshotReader, SharedSnapshotWriter};

use crate::{ContractError, ContractResult, SnapshotEnvelope};

pub struct MmapSnapshotPublisher {
    writer: SharedSnapshotWriter,
}

impl MmapSnapshotPublisher {
    pub fn create(path: impl AsRef<Path>, slot_size: usize) -> ContractResult<Self> {
        Ok(Self {
            writer: SharedSnapshotWriter::create(path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }

    pub fn publish(&mut self, snapshot: &SnapshotEnvelope) -> ContractResult<()> {
        self.writer
            .publish(snapshot.generation, &snapshot.payload)
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

pub struct MmapSnapshotReader {
    reader: SharedSnapshotReader,
}

impl MmapSnapshotReader {
    pub fn open(path: impl AsRef<Path>) -> ContractResult<Self> {
        Ok(Self {
            reader: SharedSnapshotReader::open(path)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }

    pub fn read_payload(&self) -> ContractResult<kairos_transport::SharedSnapshotPayload> {
        self.reader.read_payload().map_err(ContractError::Transport)
    }
}
