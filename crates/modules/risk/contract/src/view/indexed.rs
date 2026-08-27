use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use kairos_indexed_view::{
    EnvironmentOptions, IndexedViewIdentity, IndexedViewReader, MetadataSnapshot, PrefixRequest,
    SchemaDescriptor, SchemaSet, environment_path,
};
use kairos_primitives::runtime::{ActorId, InstanceIdentity};
use kairos_protocol::generated::kairos::risk::v_2 as fb;

use crate::{ContractError, ContractResult};

pub const RISK_STATE_DATABASE: &str = "state";
pub const RISK_POLICIES_DATABASE: &str = "policies";
pub const RISK_LIMIT_USAGE_DATABASE: &str = "limit_usage";
pub const RISK_ALLOCATIONS_DATABASE: &str = "allocations";
pub const RISK_RESERVATIONS_DATABASE: &str = "reservations";
pub const RISK_CIRCUITS_DATABASE: &str = "circuits";
pub const RISK_RESOURCE_EPOCH: u64 = 1;
pub const RISK_MAP_SIZE: usize = 128 * 1024 * 1024;
pub const MAX_RISK_INDEXED_VALUES_PER_DATABASE: usize = 100_000;
const KEY_VERSION: u8 = 1;
const ALL_VALUES_PREFIX: [u8; 1] = [KEY_VERSION];

pub fn risk_indexed_schema_set() -> SchemaSet {
    SchemaSet::new([
        SchemaDescriptor::new(RISK_STATE_DATABASE, 1, "RSM3", 1).expect("static Risk schema"),
        SchemaDescriptor::new(RISK_POLICIES_DATABASE, 1, "RPO3", 1).expect("static Risk schema"),
        SchemaDescriptor::new(RISK_LIMIT_USAGE_DATABASE, 1, "RLU3", 1).expect("static Risk schema"),
        SchemaDescriptor::new(RISK_ALLOCATIONS_DATABASE, 1, "RAL3", 1).expect("static Risk schema"),
        SchemaDescriptor::new(RISK_RESERVATIONS_DATABASE, 1, "RRS3", 1)
            .expect("static Risk schema"),
        SchemaDescriptor::new(RISK_CIRCUITS_DATABASE, 1, "RCI3", 1).expect("static Risk schema"),
    ])
    .expect("static Risk databases are unique")
}

pub fn risk_indexed_identity(
    identity: &InstanceIdentity,
    actor_id: &ActorId,
    producer_incarnation: u64,
) -> IndexedViewIdentity {
    IndexedViewIdentity::new(
        identity.workspace_id.to_string(),
        identity.launch_id().map(ToString::to_string),
        identity.instance_id().map(ToString::to_string),
        "Risk",
        format!("risk-{actor_id}"),
        RISK_RESOURCE_EPOCH,
        producer_incarnation,
        risk_indexed_schema_set(),
    )
    .expect("validated Risk identity produces indexed-view identity")
}

pub fn risk_indexed_environment_path(
    root: impl AsRef<Path>,
    identity: &InstanceIdentity,
    actor_id: &ActorId,
) -> ContractResult<PathBuf> {
    environment_path(root, &risk_indexed_identity(identity, actor_id, 1))
        .map_err(|error| ContractError::Transport(error.to_string()))
}

pub fn risk_indexed_key(parts: &[&str]) -> ContractResult<Vec<u8>> {
    if parts.is_empty() {
        return Err(ContractError::Invalid(
            "Risk indexed key requires at least one component".into(),
        ));
    }
    let mut key = vec![KEY_VERSION];
    for part in parts {
        if part.is_empty() || part.trim() != *part || part.as_bytes().contains(&0) {
            return Err(ContractError::Invalid(
                "Risk indexed key components must be non-empty and trimmed".into(),
            ));
        }
        let length: u16 = part
            .len()
            .try_into()
            .map_err(|_| ContractError::Invalid("Risk indexed key component is too long".into()))?;
        key.extend_from_slice(&length.to_be_bytes());
        key.extend_from_slice(part.as_bytes());
    }
    Ok(key)
}

pub struct RiskIndexedView {
    reader: IndexedViewReader,
    actor_id: ActorId,
}

impl RiskIndexedView {
    pub fn open(
        root: impl AsRef<Path>,
        identity: &InstanceIdentity,
        actor_id: ActorId,
    ) -> ContractResult<Self> {
        let options = EnvironmentOptions::new(
            risk_indexed_environment_path(root, identity, &actor_id)?,
            RISK_MAP_SIZE,
        )
        .map_err(|error| ContractError::Transport(error.to_string()))?;
        let reader =
            IndexedViewReader::open(&options, risk_indexed_identity(identity, &actor_id, 1))
                .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { reader, actor_id })
    }

    pub fn snapshot(&self) -> ContractResult<RiskIndexedSnapshot> {
        let databases = [
            RISK_STATE_DATABASE,
            RISK_POLICIES_DATABASE,
            RISK_LIMIT_USAGE_DATABASE,
            RISK_ALLOCATIONS_DATABASE,
            RISK_RESERVATIONS_DATABASE,
            RISK_CIRCUITS_DATABASE,
        ];
        let requests = databases.map(|database| PrefixRequest {
            database,
            prefix: &ALL_VALUES_PREFIX,
            limit: MAX_RISK_INDEXED_VALUES_PER_DATABASE + 1,
        });
        let snapshot = self
            .reader
            .snapshot(&requests)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        if snapshot.metadata.rebuild_state != kairos_indexed_view::RebuildState::Ready {
            return Err(ContractError::Transport(
                "Risk indexed current view is not ready".into(),
            ));
        }
        ensure_bounded_rows(&snapshot.rows)?;
        Ok(RiskIndexedSnapshot {
            metadata: snapshot.metadata,
            actor_id: self.actor_id.clone(),
            rows: snapshot.rows,
        })
    }
}

fn ensure_bounded_rows(rows: &BTreeMap<String, Vec<(Vec<u8>, Vec<u8>)>>) -> ContractResult<()> {
    for (database, values) in rows {
        if values.len() > MAX_RISK_INDEXED_VALUES_PER_DATABASE {
            return Err(ContractError::Invalid(format!(
                "Risk indexed database `{database}` exceeds its read bound"
            )));
        }
    }
    Ok(())
}

pub struct RiskIndexedSnapshot {
    metadata: MetadataSnapshot,
    actor_id: ActorId,
    rows: BTreeMap<String, Vec<(Vec<u8>, Vec<u8>)>>,
}

impl RiskIndexedSnapshot {
    pub fn metadata(&self) -> &MetadataSnapshot {
        &self.metadata
    }

    pub fn state(&self) -> ContractResult<fb::RiskStateCurrent<'_>> {
        let values = self
            .rows
            .get(RISK_STATE_DATABASE)
            .map(Vec::as_slice)
            .unwrap_or_default();
        if values.len() != 1 {
            return Err(ContractError::Invalid(
                "Risk indexed snapshot must contain exactly one state value".into(),
            ));
        }
        let (key, bytes) = &values[0];
        let value = decode(
            bytes,
            fb::risk_state_current_buffer_has_identifier,
            fb::root_as_risk_state_current,
            "RSM3 RiskStateCurrent",
        )?;
        if value.actor_id() != self.actor_id.as_str()
            || decode_key_components(key)? != [value.actor_id()]
        {
            return Err(ContractError::Invalid(
                "Risk indexed key/value identity mismatch".into(),
            ));
        }
        Ok(value)
    }

    pub fn policies(&self) -> Vec<RiskIndexedViewValue> {
        self.values(RISK_POLICIES_DATABASE)
    }

    pub fn limit_usage(&self) -> Vec<RiskIndexedViewValue> {
        self.values(RISK_LIMIT_USAGE_DATABASE)
    }

    pub fn allocations(&self) -> Vec<RiskIndexedViewValue> {
        self.values(RISK_ALLOCATIONS_DATABASE)
    }

    pub fn reservations(&self) -> Vec<RiskIndexedViewValue> {
        self.values(RISK_RESERVATIONS_DATABASE)
    }

    pub fn circuits(&self) -> Vec<RiskIndexedViewValue> {
        self.values(RISK_CIRCUITS_DATABASE)
    }

    fn values(&self, database: &str) -> Vec<RiskIndexedViewValue> {
        self.rows
            .get(database)
            .into_iter()
            .flatten()
            .map(|(key, bytes)| RiskIndexedViewValue {
                actor_id: self.actor_id.clone(),
                key: key.clone(),
                bytes: bytes.clone(),
            })
            .collect()
    }
}

pub struct RiskIndexedViewValue {
    actor_id: ActorId,
    key: Vec<u8>,
    bytes: Vec<u8>,
}

impl RiskIndexedViewValue {
    pub fn state(&self) -> ContractResult<fb::RiskStateCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::risk_state_current_buffer_has_identifier,
            fb::root_as_risk_state_current,
            "RSM3 RiskStateCurrent",
        )?;
        self.validate(&[value.actor_id()], value.actor_id())?;
        Ok(value)
    }

    pub fn policy(&self) -> ContractResult<fb::RiskPolicyCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::risk_policy_current_buffer_has_identifier,
            fb::root_as_risk_policy_current,
            "RPO3 RiskPolicyCurrent",
        )?;
        self.validate(&[value.policy().policy_id()], value.actor_id())?;
        Ok(value)
    }

    pub fn limit_usage(&self) -> ContractResult<fb::RiskLimitUsageCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::risk_limit_usage_current_buffer_has_identifier,
            fb::root_as_risk_limit_usage_current,
            "RLU3 RiskLimitUsageCurrent",
        )?;
        self.validate(&[value.policy_id()], value.actor_id())?;
        Ok(value)
    }

    pub fn allocation(&self) -> ContractResult<fb::RiskAllocationCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::risk_allocation_current_buffer_has_identifier,
            fb::root_as_risk_allocation_current,
            "RAL3 RiskAllocationCurrent",
        )?;
        let allocation = value.allocation();
        self.validate(
            &[
                value.reservation_id(),
                allocation.policy_id(),
                allocation.metric().variant_name().unwrap_or("UNSPECIFIED"),
            ],
            value.actor_id(),
        )?;
        Ok(value)
    }

    pub fn reservation(&self) -> ContractResult<fb::RiskReservationCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::risk_reservation_current_buffer_has_identifier,
            fb::root_as_risk_reservation_current,
            "RRS3 RiskReservationCurrent",
        )?;
        self.validate(&[value.reservation().reservation_id()], value.actor_id())?;
        Ok(value)
    }

    pub fn circuit(&self) -> ContractResult<fb::RiskCircuitCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::risk_circuit_current_buffer_has_identifier,
            fb::root_as_risk_circuit_current,
            "RCI3 RiskCircuitCurrent",
        )?;
        self.validate(&[value.circuit_key()], value.actor_id())?;
        Ok(value)
    }

    fn validate(&self, parts: &[&str], actor_id: &str) -> ContractResult<()> {
        if actor_id != self.actor_id.as_str() || decode_key_components(&self.key)? != parts {
            return Err(ContractError::Invalid(
                "Risk indexed key/value identity mismatch".into(),
            ));
        }
        Ok(())
    }
}

fn decode<'a, T>(
    bytes: &'a [u8],
    has_identifier: impl Fn(&[u8]) -> bool,
    root: impl Fn(&'a [u8]) -> Result<T, flatbuffers::InvalidFlatbuffer>,
    expected: &str,
) -> ContractResult<T> {
    if !has_identifier(bytes) {
        return Err(ContractError::Invalid(format!("expected {expected}")));
    }
    root(bytes).map_err(|error| ContractError::Invalid(error.to_string()))
}

fn decode_key_components(key: &[u8]) -> ContractResult<Vec<&str>> {
    if key.first() != Some(&KEY_VERSION) {
        return Err(ContractError::Invalid(
            "invalid Risk indexed key version".into(),
        ));
    }
    let mut offset = 1;
    let mut parts = Vec::new();
    while offset < key.len() {
        let length_bytes = key
            .get(offset..offset + 2)
            .ok_or_else(|| ContractError::Invalid("truncated Risk indexed key".into()))?;
        let length = u16::from_be_bytes([length_bytes[0], length_bytes[1]]) as usize;
        offset += 2;
        let bytes = key
            .get(offset..offset + length)
            .ok_or_else(|| ContractError::Invalid("truncated Risk indexed key".into()))?;
        parts.push(
            std::str::from_utf8(bytes)
                .map_err(|error| ContractError::Invalid(error.to_string()))?,
        );
        offset += length;
    }
    Ok(parts)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn composite_keys_are_unambiguous() {
        assert_ne!(
            risk_indexed_key(&["reservation", "policy:margin"]).unwrap(),
            risk_indexed_key(&["reservation:policy", "margin"]).unwrap()
        );
    }
}
