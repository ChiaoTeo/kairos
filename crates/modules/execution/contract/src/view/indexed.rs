use std::path::{Path, PathBuf};

use kairos_indexed_view::{
    EnvironmentOptions, IndexedViewIdentity, IndexedViewReader, MetadataSnapshot, SchemaDescriptor,
    SchemaSet, environment_path,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::execution::v_2 as fb;

use crate::{ContractError, ContractResult};

pub const ORDERS_DATABASE: &str = "orders";
pub const INTENTS_DATABASE: &str = "intents";
pub const ALGORITHM_RUNS_DATABASE: &str = "algorithm_runs";
pub const COMMITMENTS_DATABASE: &str = "commitments";
pub const RISK_RESERVATIONS_DATABASE: &str = "risk_reservations";
pub const UNKNOWN_REMOTE_ORDERS_DATABASE: &str = "unknown_remote_orders";
pub const EXECUTION_RESOURCE_EPOCH: u64 = 1;
pub const EXECUTION_MAP_SIZE: usize = 128 * 1024 * 1024;
pub const MAX_EXECUTION_INDEXED_VALUES_PER_DATABASE: usize = 100_000;
const ENTITY_KEY_VERSION: u8 = 1;
const ENTITY_PREFIX: [u8; 1] = [ENTITY_KEY_VERSION];

pub fn execution_indexed_schema_set() -> SchemaSet {
    SchemaSet::new([
        SchemaDescriptor::new(ORDERS_DATABASE, 1, "EOR3", 1).expect("static Execution schema"),
        SchemaDescriptor::new(INTENTS_DATABASE, 1, "EIN3", 1).expect("static Execution schema"),
        SchemaDescriptor::new(ALGORITHM_RUNS_DATABASE, 1, "EAR3", 1)
            .expect("static Execution schema"),
        SchemaDescriptor::new(COMMITMENTS_DATABASE, 1, "ECO3", 1).expect("static Execution schema"),
        SchemaDescriptor::new(RISK_RESERVATIONS_DATABASE, 1, "ERR3", 1)
            .expect("static Execution schema"),
        SchemaDescriptor::new(UNKNOWN_REMOTE_ORDERS_DATABASE, 1, "EUR3", 1)
            .expect("static Execution schema"),
    ])
    .expect("static Execution databases are unique")
}

pub fn execution_indexed_identity(
    identity: &InstanceIdentity,
    producer_incarnation: u64,
) -> IndexedViewIdentity {
    IndexedViewIdentity::new(
        identity.workspace_id.to_string(),
        identity.launch_id().map(ToString::to_string),
        identity.instance_id().map(ToString::to_string),
        "Execution",
        "execution-main",
        EXECUTION_RESOURCE_EPOCH,
        producer_incarnation,
        execution_indexed_schema_set(),
    )
    .expect("validated runtime identity produces a valid indexed-view identity")
}

pub fn execution_indexed_environment_path(
    root: impl AsRef<Path>,
    identity: &InstanceIdentity,
) -> ContractResult<PathBuf> {
    environment_path(root, &execution_indexed_identity(identity, 1))
        .map_err(|error| ContractError::Transport(error.to_string()))
}

pub fn indexed_entity_key(value: &str) -> ContractResult<Vec<u8>> {
    if value.is_empty() || value.trim() != value || value.as_bytes().contains(&0) {
        return Err(ContractError::Invalid(
            "indexed current-view identity must be non-empty and trimmed".into(),
        ));
    }
    let length: u16 = value
        .len()
        .try_into()
        .map_err(|_| ContractError::Invalid("indexed current-view identity is too long".into()))?;
    let mut key = Vec::with_capacity(value.len() + 3);
    key.push(ENTITY_KEY_VERSION);
    key.extend_from_slice(&length.to_be_bytes());
    key.extend_from_slice(value.as_bytes());
    Ok(key)
}

pub struct ExecutionIndexedView {
    reader: IndexedViewReader,
}

impl ExecutionIndexedView {
    pub fn open(root: impl AsRef<Path>, identity: &InstanceIdentity) -> ContractResult<Self> {
        let path = execution_indexed_environment_path(root, identity)?;
        let options = EnvironmentOptions::new(path, EXECUTION_MAP_SIZE)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        let reader = IndexedViewReader::open(&options, execution_indexed_identity(identity, 1))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { reader })
    }

    pub fn metadata(&self) -> ContractResult<MetadataSnapshot> {
        self.reader
            .metadata()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }

    pub fn ensure_ready(&self) -> ContractResult<MetadataSnapshot> {
        let metadata = self.metadata()?;
        if metadata.rebuild_state != kairos_indexed_view::RebuildState::Ready {
            return Err(ContractError::Transport(
                "Execution indexed current view is not ready".into(),
            ));
        }
        Ok(metadata)
    }

    pub fn order(&self, order_id: &str) -> ContractResult<Option<ExecutionIndexedViewValue>> {
        self.value(ORDERS_DATABASE, order_id)
    }

    pub fn intent(&self, intent_id: &str) -> ContractResult<Option<ExecutionIndexedViewValue>> {
        self.value(INTENTS_DATABASE, intent_id)
    }

    /// Decodes an order directly from LMDB while the short read transaction is
    /// alive. The FlatBuffer root cannot escape this callback.
    pub fn with_order<R>(
        &self,
        order_id: &str,
        read: impl FnOnce(Option<fb::ExecutionOrderCurrent<'_>>) -> ContractResult<R>,
    ) -> ContractResult<R> {
        let key = indexed_entity_key(order_id)?;
        self.reader
            .with_value_snapshot(ORDERS_DATABASE, &key, |metadata, bytes| {
                if metadata.rebuild_state != kairos_indexed_view::RebuildState::Ready {
                    return Err(ContractError::Transport(
                        "Execution indexed current view is not ready".into(),
                    ));
                }
                let value = bytes
                    .map(|bytes| {
                        decode(
                            bytes,
                            fb::execution_order_current_buffer_has_identifier,
                            fb::root_as_execution_order_current,
                            "EOR3 ExecutionOrderCurrent",
                        )
                    })
                    .transpose()?;
                if let Some(value) = value {
                    validate_semantic_identity(order_id, value.state().order_id(), "order_id")?;
                    read(Some(value))
                } else {
                    read(None)
                }
            })
            .map_err(|error| ContractError::Transport(error.to_string()))?
    }

    pub fn orders(&self) -> ContractResult<Vec<ExecutionIndexedViewValue>> {
        self.values(ORDERS_DATABASE)
    }

    pub fn intents(&self) -> ContractResult<Vec<ExecutionIndexedViewValue>> {
        self.values(INTENTS_DATABASE)
    }

    pub fn algorithm_runs(&self) -> ContractResult<Vec<ExecutionIndexedViewValue>> {
        self.values(ALGORITHM_RUNS_DATABASE)
    }

    pub fn commitments(&self) -> ContractResult<Vec<ExecutionIndexedViewValue>> {
        self.values(COMMITMENTS_DATABASE)
    }

    pub fn risk_reservations(&self) -> ContractResult<Vec<ExecutionIndexedViewValue>> {
        self.values(RISK_RESERVATIONS_DATABASE)
    }

    pub fn unknown_remote_orders(&self) -> ContractResult<Vec<ExecutionIndexedViewValue>> {
        self.values(UNKNOWN_REMOTE_ORDERS_DATABASE)
    }

    fn value(
        &self,
        database: &str,
        identity: &str,
    ) -> ContractResult<Option<ExecutionIndexedViewValue>> {
        let key = indexed_entity_key(identity)?;
        self.reader
            .value_snapshot(database, &key)
            .map_err(|error| ContractError::Transport(error.to_string()))
            .and_then(|snapshot| {
                if snapshot.metadata.rebuild_state != kairos_indexed_view::RebuildState::Ready {
                    return Err(ContractError::Transport(
                        "Execution indexed current view is not ready".into(),
                    ));
                }
                Ok(snapshot
                    .value
                    .map(|bytes| ExecutionIndexedViewValue::new(key, bytes)))
            })
    }

    fn values(&self, database: &str) -> ContractResult<Vec<ExecutionIndexedViewValue>> {
        let (metadata, rows) = self
            .reader
            .map_prefix_snapshot(
                database,
                &ENTITY_PREFIX,
                MAX_EXECUTION_INDEXED_VALUES_PER_DATABASE + 1,
                |_metadata, key, value| (key.to_owned(), value.to_owned()),
            )
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        if metadata.rebuild_state != kairos_indexed_view::RebuildState::Ready {
            return Err(ContractError::Transport(
                "Execution indexed current view is not ready".into(),
            ));
        }
        if rows.len() > MAX_EXECUTION_INDEXED_VALUES_PER_DATABASE {
            return Err(ContractError::Invalid(format!(
                "Execution indexed database `{database}` exceeds its read bound"
            )));
        }
        Ok(rows
            .into_iter()
            .map(|(key, value)| ExecutionIndexedViewValue::new(key, value))
            .collect())
    }
}

pub struct ExecutionIndexedViewValue {
    key: Vec<u8>,
    bytes: Vec<u8>,
}

impl ExecutionIndexedViewValue {
    fn new(key: Vec<u8>, bytes: Vec<u8>) -> Self {
        Self { key, bytes }
    }

    pub fn identity(&self) -> ContractResult<&str> {
        indexed_entity_identity(&self.key)
    }

    pub fn order(&self) -> ContractResult<fb::ExecutionOrderCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::execution_order_current_buffer_has_identifier,
            fb::root_as_execution_order_current,
            "EOR3 ExecutionOrderCurrent",
        )?;
        validate_semantic_identity(self.identity()?, value.state().order_id(), "order_id")?;
        Ok(value)
    }

    pub fn intent(&self) -> ContractResult<fb::ExecutionIntentCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::execution_intent_current_buffer_has_identifier,
            fb::root_as_execution_intent_current,
            "EIN3 ExecutionIntentCurrent",
        )?;
        validate_semantic_identity(
            self.identity()?,
            value.state().intent().intent_id(),
            "intent_id",
        )?;
        Ok(value)
    }

    pub fn algorithm_run(&self) -> ContractResult<fb::ExecutionAlgorithmRunCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::execution_algorithm_run_current_buffer_has_identifier,
            fb::root_as_execution_algorithm_run_current,
            "EAR3 ExecutionAlgorithmRunCurrent",
        )?;
        validate_semantic_identity(
            self.identity()?,
            value.state().algorithm_run_id(),
            "algorithm_run_id",
        )?;
        Ok(value)
    }

    pub fn commitment(&self) -> ContractResult<fb::ExecutionCommitmentCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::execution_commitment_current_buffer_has_identifier,
            fb::root_as_execution_commitment_current,
            "ECO3 ExecutionCommitmentCurrent",
        )?;
        validate_semantic_identity(self.identity()?, value.state().order_id(), "order_id")?;
        Ok(value)
    }

    pub fn risk_reservation(&self) -> ContractResult<fb::ExecutionRiskReservationCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::execution_risk_reservation_current_buffer_has_identifier,
            fb::root_as_execution_risk_reservation_current,
            "ERR3 ExecutionRiskReservationCurrent",
        )?;
        validate_semantic_identity(
            self.identity()?,
            value.state().reservation_id(),
            "reservation_id",
        )?;
        Ok(value)
    }

    pub fn unknown_remote_order(
        &self,
    ) -> ContractResult<fb::ExecutionUnknownRemoteOrderCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::execution_unknown_remote_order_current_buffer_has_identifier,
            fb::root_as_execution_unknown_remote_order_current,
            "EUR3 ExecutionUnknownRemoteOrderCurrent",
        )?;
        validate_semantic_identity(
            self.identity()?,
            value.state().remote_order_id(),
            "remote_order_id",
        )?;
        Ok(value)
    }
}

fn indexed_entity_identity(key: &[u8]) -> ContractResult<&str> {
    if key.len() < 3 || key[0] != ENTITY_KEY_VERSION {
        return Err(ContractError::Invalid(
            "invalid Execution indexed entity key version".into(),
        ));
    }
    let length = usize::from(u16::from_be_bytes([key[1], key[2]]));
    if key.len() != length + 3 {
        return Err(ContractError::Invalid(
            "invalid Execution indexed entity key length".into(),
        ));
    }
    std::str::from_utf8(&key[3..])
        .map_err(|error| ContractError::Invalid(format!("invalid Execution entity key: {error}")))
}

fn validate_semantic_identity(expected: &str, actual: &str, field: &str) -> ContractResult<()> {
    if actual != expected {
        return Err(ContractError::Invalid(format!(
            "Execution indexed key/value identity mismatch: key={expected}, {field}={actual}"
        )));
    }
    Ok(())
}

fn decode<'a, T>(
    bytes: &'a [u8],
    has_identifier: impl Fn(&[u8]) -> bool,
    root: impl Fn(&'a [u8]) -> Result<T, flatbuffers::InvalidFlatbuffer>,
    expected: &str,
) -> ContractResult<T> {
    if !kairos_protocol::flatbuffer::identifier_is_readable(bytes) {
        return Err(ContractError::Invalid(format!(
            "truncated {expected} FlatBuffers value"
        )));
    }
    if !has_identifier(bytes) {
        return Err(ContractError::Invalid(format!("expected {expected}")));
    }
    root(bytes).map_err(|error| ContractError::Invalid(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn entity_keys_are_versioned_and_length_delimited() {
        assert_eq!(
            indexed_entity_key("order-1").unwrap(),
            b"\x01\x00\x07order-1"
        );
    }

    #[test]
    fn entity_key_decoder_rejects_trailing_or_wrong_version_bytes() {
        assert_eq!(indexed_entity_identity(b"\x01\x00\x03abc").unwrap(), "abc");
        assert!(indexed_entity_identity(b"\x02\x00\x03abc").is_err());
        assert!(indexed_entity_identity(b"\x01\x00\x02abc").is_err());
    }
}
