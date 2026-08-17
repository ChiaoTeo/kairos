mod account_current;
mod key;
mod metadata;
mod observed_orders;

pub use account_current::{decode as decode_account_current, AccountCurrentView};
pub use key::{AccountViewKey, AccountViewKind};
pub use metadata::ViewMetadata;
pub use observed_orders::ObservedOrdersCurrentView;

use crate::{ContractError, ContractResult};
use kairos_transport::{
    ReplacementSnapshotStorage, SharedSnapshotReader, SnapshotEnvelopeMetadata,
};
use std::path::Path;

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
    pub fn account_current(&self) -> ContractResult<AccountCurrentView<'_>> {
        account_current::decode(self.bytes())
    }
    pub fn observed_orders(&self) -> ContractResult<ObservedOrdersCurrentView<'_>> {
        observed_orders::decode(self.bytes())
    }
}

pub struct AccountViewReader {
    key: AccountViewKey,
    reader: SharedSnapshotReader,
}
impl AccountViewReader {
    pub fn open(root: impl AsRef<Path>, key: AccountViewKey) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(key.resource_path(root))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, reader })
    }
    pub fn key(&self) -> &AccountViewKey {
        &self.key
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
}

pub struct AccountViewPublisher {
    key: AccountViewKey,
    writer: ReplacementSnapshotStorage,
}
impl AccountViewPublisher {
    pub fn create(
        root: impl AsRef<Path>,
        key: AccountViewKey,
        slot_size: usize,
    ) -> ContractResult<Self> {
        let writer = ReplacementSnapshotStorage::create(key.resource_path(root), slot_size)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, writer })
    }
    pub fn key(&self) -> &AccountViewKey {
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
