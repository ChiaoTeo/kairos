//! KSS1 mmap adapters for Reference snapshots.

use std::path::Path;

use kairos_transport::{SharedSnapshotReader, SharedSnapshotWriter};

use crate::{ContractError, ContractResult, SnapshotEnvelope, SnapshotPublisher, SnapshotReader};

pub struct MmapSnapshotPublisher {
    writer: SharedSnapshotWriter,
}

impl MmapSnapshotPublisher {
    pub fn create(path: impl AsRef<Path>, slot_size: usize) -> ContractResult<Self> {
        let writer = SharedSnapshotWriter::create(path, slot_size)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { writer })
    }
}

impl SnapshotPublisher for MmapSnapshotPublisher {
    fn publish(&mut self, snapshot: &SnapshotEnvelope) -> ContractResult<()> {
        if snapshot.payload.is_empty() {
            return Err(ContractError::Invalid(
                "snapshot payload must not be empty".into(),
            ));
        }
        self.writer
            .publish(snapshot.generation, &snapshot.payload)
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

pub struct MmapSnapshotReader {
    reader: SharedSnapshotReader,
    view_key: String,
    producer_id: String,
    event_stream_id: String,
}

impl MmapSnapshotReader {
    pub fn open(
        path: impl AsRef<Path>,
        view_key: impl Into<String>,
        producer_id: impl Into<String>,
        event_stream_id: impl Into<String>,
    ) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(path)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self {
            reader,
            view_key: view_key.into(),
            producer_id: producer_id.into(),
            event_stream_id: event_stream_id.into(),
        })
    }
}

impl SnapshotReader for MmapSnapshotReader {
    fn read(&self) -> ContractResult<SnapshotEnvelope> {
        let payload = self
            .reader
            .read_payload()
            .map_err(ContractError::Transport)?;
        Ok(SnapshotEnvelope {
            view_key: self.view_key.clone(),
            producer_id: self.producer_id.clone(),
            event_stream_id: self.event_stream_id.clone(),
            generation: payload.generation,
            event_sequence: 0,
            published_at_unix_nanos: 0,
            payload: payload.payload,
        })
    }
}
