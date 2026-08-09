use std::path::Path;

use crate::reference::{decode_reference_changed, ReferenceChangeNotice};
use kairos_transport::{SharedSnapshotReader, SharedSnapshotWriter};
use std::collections::VecDeque;

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

pub struct AeronReferenceChangeSource {
    subscription: kairos_transport::AeronByteSubscription,
    queue: VecDeque<ReferenceChangeNotice>,
}

impl AeronReferenceChangeSource {
    pub fn connect(aeron_dir: Option<&str>, channel: &str, stream_id: i32) -> ContractResult<Self> {
        let subscription =
            kairos_transport::AeronByteSubscription::connect(aeron_dir, channel, stream_id)
                .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self {
            subscription,
            queue: VecDeque::new(),
        })
    }

    pub fn next_change(&mut self) -> ContractResult<Option<ReferenceChangeNotice>> {
        while let Some(frame) = self.subscription.next().map_err(ContractError::Transport)? {
            self.queue
                .push_back(decode_reference_changed(&frame).map_err(ContractError::Invalid)?);
        }
        Ok(self.queue.pop_front())
    }
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
