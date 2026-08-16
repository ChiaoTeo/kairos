mod account_current;
mod key;
mod metadata;
mod observed_orders;

pub use account_current::AccountCurrentView;
pub use key::{AccountViewKey, AccountViewKind};
pub use metadata::ViewMetadata;
pub use observed_orders::ObservedOrdersCurrentView;

use crate::{ContractError, ContractResult};
use kairos_transport::SharedSnapshotWriter;
use std::path::Path;

pub struct ViewFrame {
    generation: u64,
    bytes: Vec<u8>,
}
impl ViewFrame {
    pub(crate) fn new(generation: u64, bytes: Vec<u8>) -> Self {
        Self { generation, bytes }
    }
    pub fn generation(&self) -> u64 {
        self.generation
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
    reader: crate::transport::AccountMmapReader,
}
impl AccountViewReader {
    pub fn open(root: impl AsRef<Path>, key: AccountViewKey) -> ContractResult<Self> {
        Ok(Self {
            key: key.clone(),
            reader: crate::transport::AccountMmapReader::open(root, key)?,
        })
    }
    pub fn key(&self) -> &AccountViewKey {
        &self.key
    }
    pub fn read(&self) -> ContractResult<ViewFrame> {
        self.reader.read()
    }
}

pub struct AccountViewPublisher {
    key: AccountViewKey,
    writer: SharedSnapshotWriter,
}
impl AccountViewPublisher {
    pub fn create(
        root: impl AsRef<Path>,
        key: AccountViewKey,
        slot_size: usize,
    ) -> ContractResult<Self> {
        let writer = SharedSnapshotWriter::create(key.resource_path(root), slot_size)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, writer })
    }
    pub fn key(&self) -> &AccountViewKey {
        &self.key
    }
    pub fn publish(&mut self, generation: u64, payload: &[u8]) -> ContractResult<()> {
        self.writer
            .publish(generation, payload)
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}
