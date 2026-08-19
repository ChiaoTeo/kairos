pub mod encode;
mod key;
mod metadata;
use std::path::{Path, PathBuf};

use kairos_transport::{
    ReplacementSnapshotStorage, SharedSnapshotReader, SnapshotEnvelopeMetadata,
};
pub use key::{RiskViewKey, RiskViewKind};
pub use metadata::ViewMetadata;

use crate::{ContractError, ContractResult};

pub fn risk_view_path(root: impl AsRef<Path>, key: &RiskViewKey) -> ContractResult<PathBuf> {
    key.resource_path(root)
}
pub struct ViewFrame {
    metadata: SnapshotEnvelopeMetadata,
    bytes: Vec<u8>,
}
impl ViewFrame {
    pub(crate) fn new(metadata: SnapshotEnvelopeMetadata, bytes: Vec<u8>) -> Self {
        Self { metadata, bytes }
    }
    pub fn generation(&self) -> u64 {
        self.metadata.generation
    }
    pub fn envelope_metadata(&self) -> SnapshotEnvelopeMetadata {
        self.metadata
    }
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }
    pub fn decode(
        &self,
    ) -> ContractResult<kairos_protocol::generated::kairos::risk::v_2::RiskLatestView<'_>> {
        if !kairos_protocol::generated::kairos::risk::v_2::risk_latest_view_buffer_has_identifier(
            &self.bytes,
        ) {
            return Err(ContractError::Invalid(
                "expected RXV2 RiskLatestView".into(),
            ));
        }
        kairos_protocol::generated::kairos::risk::v_2::root_as_risk_latest_view(&self.bytes)
            .map_err(|e| ContractError::Invalid(e.to_string()))
    }
}
pub struct RiskViewReader {
    key: RiskViewKey,
    reader: SharedSnapshotReader,
}
impl RiskViewReader {
    pub fn resolved_path(root: impl AsRef<Path>, key: &RiskViewKey) -> ContractResult<PathBuf> {
        risk_view_path(root, key)
    }

    pub fn open(root: impl AsRef<Path>, key: RiskViewKey) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(risk_view_path(root, &key)?)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { reader, key })
    }
    pub fn read(&self) -> ContractResult<ViewFrame> {
        let frame = self
            .reader
            .read_payload()
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(ViewFrame::new(
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
    pub fn key(&self) -> &RiskViewKey {
        &self.key
    }
}

pub struct RiskViewPublisher {
    key: RiskViewKey,
    writer: ReplacementSnapshotStorage,
}

impl RiskViewPublisher {
    pub fn resolved_path(root: impl AsRef<Path>, key: &RiskViewKey) -> ContractResult<PathBuf> {
        risk_view_path(root, key)
    }

    pub fn create(
        root: impl AsRef<Path>,
        key: RiskViewKey,
        slot_capacity: usize,
    ) -> ContractResult<Self> {
        let writer = ReplacementSnapshotStorage::create(risk_view_path(root, &key)?, slot_capacity)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, writer })
    }

    pub fn key(&self) -> &RiskViewKey {
        &self.key
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
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reader_and_publisher_resolve_the_same_safe_path() {
        let key = RiskViewKey::latest("risk/../一");
        let root = Path::new("/tmp/risk-contract-views");
        let reader = RiskViewReader::resolved_path(root, &key).unwrap();
        let publisher = RiskViewPublisher::resolved_path(root, &key).unwrap();
        assert_eq!(reader, publisher);
        assert!(reader.starts_with(root));
        assert!(!reader.to_string_lossy().contains("/../"));
    }

    #[test]
    fn publisher_output_is_readable_through_the_contract_reader() {
        let root = tempfile::tempdir().unwrap();
        let key = RiskViewKey::latest("risk");
        let mut publisher = RiskViewPublisher::create(root.path(), key.clone(), 4096).unwrap();
        publisher.publish(test_metadata(), b"risk-view").unwrap();
        let frame = RiskViewReader::open(root.path(), key)
            .unwrap()
            .read()
            .unwrap();
        assert_eq!(frame.bytes(), b"risk-view");
    }

    fn test_metadata() -> SnapshotEnvelopeMetadata {
        SnapshotEnvelopeMetadata {
            resource_epoch: 1,
            producer_incarnation: 1,
            generation: 1,
            applied_event_sequence: 1,
            published_at_unix_nanos: 1,
        }
    }
}
