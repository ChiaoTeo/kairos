pub(crate) mod encode;
mod key;

use std::path::{Path, PathBuf};

pub use encode::{FlatbuffersCapitalViewWriter, MmapCapitalViewPublisher};
use kairos_transport::{
    ReplacementSnapshotStorage, SharedSnapshotReader, SnapshotEnvelopeMetadata,
};
pub use key::CapitalViewKey;

use crate::{ContractError, ContractResult};

pub fn capital_view_path(root: impl AsRef<Path>, key: &CapitalViewKey) -> ContractResult<PathBuf> {
    key.resource_path(root)
}

pub struct CapitalViewFrame {
    metadata: SnapshotEnvelopeMetadata,
    bytes: Vec<u8>,
}

impl CapitalViewFrame {
    fn new(metadata: SnapshotEnvelopeMetadata, bytes: Vec<u8>) -> Self {
        Self { metadata, bytes }
    }

    pub fn envelope_metadata(&self) -> SnapshotEnvelopeMetadata {
        self.metadata
    }

    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn decode(
        &self,
    ) -> ContractResult<kairos_protocol::generated::kairos::capital::v_2::CapitalCurrentView<'_>>
    {
        if !kairos_protocol::generated::kairos::capital::v_2::capital_current_view_buffer_has_identifier(
            &self.bytes,
        ) {
            return Err(ContractError::Invalid(
                "expected CPV2 CapitalCurrentView".into(),
            ));
        }
        kairos_protocol::generated::kairos::capital::v_2::root_as_capital_current_view(&self.bytes)
            .map_err(|error| ContractError::Invalid(error.to_string()))
    }
}

pub struct CapitalViewReader {
    key: CapitalViewKey,
    reader: SharedSnapshotReader,
}

impl CapitalViewReader {
    pub fn resolved_path(root: impl AsRef<Path>, key: &CapitalViewKey) -> ContractResult<PathBuf> {
        capital_view_path(root, key)
    }

    pub fn open(root: impl AsRef<Path>, key: CapitalViewKey) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(capital_view_path(root, &key)?)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, reader })
    }

    pub fn read(&self) -> ContractResult<CapitalViewFrame> {
        let frame = self
            .reader
            .read_payload()
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(CapitalViewFrame::new(
            SnapshotEnvelopeMetadata {
                resource_epoch: frame.resource_epoch,
                producer_incarnation: frame.producer_incarnation,
                generation: frame.generation,
                applied_event_sequence: frame.applied_event_sequence,
                published_at_unix_nanos: frame.published_at_unix_nanos,
            },
            frame.payload,
        ))
    }

    pub fn key(&self) -> &CapitalViewKey {
        &self.key
    }
}

pub struct CapitalViewPublisher {
    key: CapitalViewKey,
    writer: ReplacementSnapshotStorage,
}

impl CapitalViewPublisher {
    pub fn create(
        root: impl AsRef<Path>,
        key: CapitalViewKey,
        slot_capacity: usize,
    ) -> ContractResult<Self> {
        let writer =
            ReplacementSnapshotStorage::create(capital_view_path(root, &key)?, slot_capacity)
                .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, writer })
    }

    pub fn publish(
        &mut self,
        metadata: SnapshotEnvelopeMetadata,
        payload: &[u8],
    ) -> ContractResult<()> {
        self.writer
            .publish(metadata, payload)
            .map(|_| ())
            .map_err(|error| ContractError::Transport(error.to_string()))
    }

    pub fn key(&self) -> &CapitalViewKey {
        &self.key
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reader_and_publisher_share_a_safe_path() {
        let root = Path::new("/tmp/capital-contract-views");
        let key = CapitalViewKey::current("group/../一");
        let reader = CapitalViewReader::resolved_path(root, &key).unwrap();
        let publisher =
            CapitalViewPublisher::create(tempfile::tempdir().unwrap().path(), key, 4096);
        assert!(publisher.is_ok());
        assert!(reader.starts_with(root));
        assert!(!reader.to_string_lossy().contains("/../"));
    }
}
