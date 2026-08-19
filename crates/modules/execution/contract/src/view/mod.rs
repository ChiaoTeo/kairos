mod active_intents;
mod active_orders;
mod current_execution;
mod key;
mod metadata;

use std::path::{Path, PathBuf};

pub use active_intents::ActiveIntentsView;
pub use active_orders::ActiveOrdersView;
pub use current_execution::CurrentExecutionView;
use kairos_transport::{
    ReplacementSnapshotStorage, SharedSnapshotReader, SnapshotEnvelopeMetadata,
};
pub use key::{ExecutionViewKey, ExecutionViewKind};
pub use metadata::ViewMetadata;

use crate::{ContractError, ContractResult};

pub fn execution_view_path(
    root: impl AsRef<Path>,
    key: &ExecutionViewKey,
) -> ContractResult<PathBuf> {
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
    pub fn active_orders(&self) -> ContractResult<ActiveOrdersView<'_>> {
        active_orders::decode(self.bytes())
    }
    pub fn active_intents(&self) -> ContractResult<ActiveIntentsView<'_>> {
        active_intents::decode(self.bytes())
    }
    pub fn current_execution(&self) -> ContractResult<CurrentExecutionView<'_>> {
        current_execution::decode(self.bytes())
    }
}

pub struct ExecutionViewReader {
    key: ExecutionViewKey,
    reader: SharedSnapshotReader,
}
impl ExecutionViewReader {
    pub fn resolved_path(
        root: impl AsRef<Path>,
        key: &ExecutionViewKey,
    ) -> ContractResult<PathBuf> {
        execution_view_path(root, key)
    }

    pub fn open(root: impl AsRef<Path>, key: ExecutionViewKey) -> ContractResult<Self> {
        let reader = SharedSnapshotReader::open(execution_view_path(root, &key)?)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, reader })
    }
    pub fn key(&self) -> &ExecutionViewKey {
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

pub struct ExecutionViewPublisher {
    key: ExecutionViewKey,
    writer: ReplacementSnapshotStorage,
}
impl ExecutionViewPublisher {
    pub fn resolved_path(
        root: impl AsRef<Path>,
        key: &ExecutionViewKey,
    ) -> ContractResult<PathBuf> {
        execution_view_path(root, key)
    }

    pub fn create(
        root: impl AsRef<Path>,
        key: ExecutionViewKey,
        slot_size: usize,
    ) -> ContractResult<Self> {
        Ok(Self {
            key: key.clone(),
            writer: ReplacementSnapshotStorage::create(execution_view_path(root, &key)?, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }
    pub fn key(&self) -> &ExecutionViewKey {
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
        let key = ExecutionViewKey::new(
            "workspace/../一",
            ExecutionViewKind::ActiveOrders,
            Some("launch"),
            Some("instance"),
        )
        .unwrap();
        let root = Path::new("/tmp/execution-contract-views");
        let reader = ExecutionViewReader::resolved_path(root, &key).unwrap();
        let publisher = ExecutionViewPublisher::resolved_path(root, &key).unwrap();
        assert_eq!(reader, publisher);
        assert!(reader.starts_with(root));
        assert!(!reader.to_string_lossy().contains("/../"));
    }

    #[test]
    fn publisher_output_is_readable_through_the_contract_reader() {
        let root = tempfile::tempdir().unwrap();
        let key = ExecutionViewKey::new(
            "workspace",
            ExecutionViewKind::ActiveOrders,
            Some("launch"),
            Some("instance"),
        )
        .unwrap();
        let mut publisher = ExecutionViewPublisher::create(root.path(), key.clone(), 4096).unwrap();
        publisher
            .publish(test_metadata(), b"execution-view")
            .unwrap();
        let frame = ExecutionViewReader::open(root.path(), key)
            .unwrap()
            .read()
            .unwrap();
        assert_eq!(frame.bytes(), b"execution-view");
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
