pub mod encode;
mod key;
mod metadata;
use crate::{ContractError, ContractResult};
use kairos_transport::{
    ReplacementSnapshotStorage, SharedSnapshotReader, SnapshotEnvelopeMetadata,
};
pub use key::{RiskViewKey, RiskViewKind};
pub use metadata::ViewMetadata;
use std::path::{Path, PathBuf};

pub fn risk_view_path(
    root: impl AsRef<Path>,
    key: &RiskViewKey,
) -> ContractResult<PathBuf> {
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
    pub fn resolved_path(
        root: impl AsRef<Path>,
        key: &RiskViewKey,
    ) -> ContractResult<PathBuf> {
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
    pub fn resolved_path(
        root: impl AsRef<Path>,
        key: &RiskViewKey,
    ) -> ContractResult<PathBuf> {
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
