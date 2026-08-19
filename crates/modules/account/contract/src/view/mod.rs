mod account_current;
mod key;
mod metadata;
mod observed_orders;

use std::path::{Path, PathBuf};

pub use account_current::{AccountCurrentView, decode as decode_account_current};
use kairos_transport::{
    ReplacementSnapshotStorage, SharedSnapshotReader, SnapshotEnvelopeMetadata,
};
pub use key::{AccountViewKey, AccountViewKind};
pub use metadata::ViewMetadata;
pub use observed_orders::ObservedOrdersCurrentView;

use crate::{ContractError, ContractResult};

pub fn account_view_path(root: impl AsRef<Path>, key: &AccountViewKey) -> ContractResult<PathBuf> {
    Ok(key.resource_path(root))
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
    pub fn resolved_path(root: impl AsRef<Path>, key: &AccountViewKey) -> ContractResult<PathBuf> {
        account_view_path(root, key)
    }

    pub fn open(root: impl AsRef<Path>, key: AccountViewKey) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(account_view_path(root, &key)?)
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
    pub fn resolved_path(root: impl AsRef<Path>, key: &AccountViewKey) -> ContractResult<PathBuf> {
        account_view_path(root, key)
    }

    pub fn create(
        root: impl AsRef<Path>,
        key: AccountViewKey,
        slot_size: usize,
    ) -> ContractResult<Self> {
        let writer = ReplacementSnapshotStorage::create(account_view_path(root, &key)?, slot_size)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reader_and_publisher_resolve_the_same_safe_path() {
        let key = AccountViewKey::new(
            "runtime/../一",
            "account%/main",
            AccountViewKind::ObservedOrders,
        )
        .unwrap();
        let root = Path::new("/tmp/account-contract-views");
        let reader = AccountViewReader::resolved_path(root, &key).unwrap();
        let publisher = AccountViewPublisher::resolved_path(root, &key).unwrap();
        assert_eq!(reader, publisher);
        assert!(reader.starts_with(root));
        assert!(!reader.to_string_lossy().contains("/../"));
    }

    #[test]
    fn publisher_output_is_readable_through_the_contract_reader() {
        let root = tempfile::tempdir().unwrap();
        let key = AccountViewKey::new("runtime", "account", AccountViewKind::Current).unwrap();
        let mut publisher = AccountViewPublisher::create(root.path(), key.clone(), 4096).unwrap();
        publisher.publish(test_metadata(), b"account-view").unwrap();
        let frame = AccountViewReader::open(root.path(), key)
            .unwrap()
            .read()
            .unwrap();
        assert_eq!(frame.bytes(), b"account-view");
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
