use std::path::{Path, PathBuf};

use crate::{SharedSnapshotWriter, SnapshotEnvelopeMetadata, SnapshotError};

/// Contract-owned mapping from a typed resource key to its transport path and identity.
pub trait SnapshotResource {
    type Key;
    type Identity;

    fn resource_path(&self, key: &Self::Key) -> PathBuf;
    fn resource_identity(&self, key: &Self::Key) -> Self::Identity;
}

/// Contract-owned typed payload codec. The platform never interprets `Model`.
pub trait SnapshotCodec<Model> {
    type Error;

    fn encode(&self, model: &Model, output: &mut Vec<u8>) -> Result<(), Self::Error>;
    fn validate(&self, bytes: &[u8]) -> Result<(), Self::Error>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SnapshotPublishReceipt {
    pub resource_epoch: u64,
    pub producer_incarnation: u64,
    pub generation: u64,
    pub applied_event_sequence: u64,
}

/// The single full-replacement storage implementation used by typed contracts.
pub struct ReplacementSnapshotStorage {
    writer: SharedSnapshotWriter,
}

impl ReplacementSnapshotStorage {
    pub fn create(path: impl AsRef<Path>, slot_capacity: usize) -> Result<Self, SnapshotError> {
        Ok(Self {
            writer: SharedSnapshotWriter::create(path, slot_capacity)?,
        })
    }

    pub fn publish(
        &mut self,
        metadata: SnapshotEnvelopeMetadata,
        payload: &[u8],
    ) -> Result<SnapshotPublishReceipt, SnapshotError> {
        self.writer.publish_with_metadata(metadata, payload)?;
        Ok(SnapshotPublishReceipt {
            resource_epoch: metadata.resource_epoch,
            producer_incarnation: metadata.producer_incarnation,
            generation: metadata.generation,
            applied_event_sequence: metadata.applied_event_sequence,
        })
    }
}
