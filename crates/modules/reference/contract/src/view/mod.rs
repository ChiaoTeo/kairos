mod encode;
mod key;
mod owned;

use crate::{ContractError, ContractResult};
pub use encode::{encode_reference_latest, MmapReferenceLatestPublisher};
use kairos_transport::{
    ReplacementSnapshotStorage, SharedSnapshotReader, SnapshotEnvelopeMetadata,
};
pub use key::{ReferenceViewKey, ReferenceViewKind};
pub use owned::decode_reference_latest;
use std::path::Path;

pub struct ReferenceViewFrame {
    metadata: SnapshotEnvelopeMetadata,
    bytes: Vec<u8>,
}

impl ReferenceViewFrame {
    pub fn generation(&self) -> u64 {
        self.metadata.generation
    }
    pub fn envelope_metadata(&self) -> SnapshotEnvelopeMetadata {
        self.metadata
    }
    pub fn decode(
        &self,
    ) -> ContractResult<kairos_protocol::generated::kairos::reference::v_2::ReferenceLatestView<'_>>
    {
        use kairos_protocol::generated::kairos::reference::v_2 as fb;
        if !fb::reference_latest_view_buffer_has_identifier(&self.bytes) {
            return Err(ContractError::Invalid(
                "expected RFV2 ReferenceLatestView".into(),
            ));
        }
        fb::root_as_reference_latest_view(&self.bytes)
            .map_err(|error| ContractError::Invalid(error.to_string()))
    }
}

pub struct ReferenceViewReader {
    key: ReferenceViewKey,
    reader: SharedSnapshotReader,
}

impl ReferenceViewReader {
    pub fn open(root: impl AsRef<Path>, key: ReferenceViewKey) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(key.resource_path(root))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, reader })
    }
    pub fn read(&self) -> ContractResult<ReferenceViewFrame> {
        let frame = self
            .reader
            .read_payload()
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(ReferenceViewFrame {
            metadata: SnapshotEnvelopeMetadata {
                resource_epoch: frame.resource_epoch,
                producer_incarnation: frame.producer_incarnation,
                generation: frame.generation,
                applied_event_sequence: frame.applied_event_sequence,
                published_at_unix_nanos: frame.published_at_unix_nanos,
            },
            bytes: frame.payload,
        })
    }
    pub fn key(&self) -> &ReferenceViewKey {
        &self.key
    }
}

pub(crate) struct ReferenceViewPublisher {
    writer: ReplacementSnapshotStorage,
}

impl ReferenceViewPublisher {
    pub(crate) fn create(
        root: impl AsRef<Path>,
        key: ReferenceViewKey,
        slot_capacity: usize,
    ) -> ContractResult<Self> {
        let writer = ReplacementSnapshotStorage::create(key.resource_path(root), slot_capacity)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { writer })
    }
    pub(crate) fn publish(
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
