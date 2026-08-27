use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use kairos_indexed_view::{
    EnvironmentOptions, IndexedViewIdentity, IndexedViewReader, MetadataSnapshot, PrefixRequest,
    SchemaDescriptor, SchemaSet, environment_path,
};
use kairos_primitives::account::AccountId;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::account::v_2 as fb;

use crate::{ContractError, ContractResult};

pub const ACCOUNT_SEGMENTS_DATABASE: &str = "segments";
pub const ACCOUNT_BALANCES_DATABASE: &str = "balances";
pub const ACCOUNT_COLLATERAL_DATABASE: &str = "collateral";
pub const ACCOUNT_POSITIONS_DATABASE: &str = "positions";
pub const ACCOUNT_VALUATIONS_DATABASE: &str = "valuations";
pub const ACCOUNT_EARN_HOLDINGS_DATABASE: &str = "earn_holdings";
pub const ACCOUNT_OBSERVED_ORDERS_DATABASE: &str = "observed_orders";
pub const ACCOUNT_RESOURCE_EPOCH: u64 = 1;
pub const ACCOUNT_MAP_SIZE: usize = 256 * 1024 * 1024;
pub const MAX_ACCOUNT_INDEXED_VALUES_PER_DATABASE: usize = 100_000;
const KEY_VERSION: u8 = 1;
const ALL_VALUES_PREFIX: [u8; 1] = [KEY_VERSION];

pub fn account_indexed_schema_set() -> SchemaSet {
    SchemaSet::new([
        SchemaDescriptor::new(ACCOUNT_SEGMENTS_DATABASE, 1, "ASG3", 1)
            .expect("static Account schema"),
        SchemaDescriptor::new(ACCOUNT_BALANCES_DATABASE, 1, "ABA3", 1)
            .expect("static Account schema"),
        SchemaDescriptor::new(ACCOUNT_COLLATERAL_DATABASE, 1, "ACO3", 1)
            .expect("static Account schema"),
        SchemaDescriptor::new(ACCOUNT_POSITIONS_DATABASE, 1, "APO3", 1)
            .expect("static Account schema"),
        SchemaDescriptor::new(ACCOUNT_VALUATIONS_DATABASE, 1, "AVL3", 1)
            .expect("static Account schema"),
        SchemaDescriptor::new(ACCOUNT_EARN_HOLDINGS_DATABASE, 1, "AEH3", 1)
            .expect("static Account schema"),
        SchemaDescriptor::new(ACCOUNT_OBSERVED_ORDERS_DATABASE, 1, "AOO3", 1)
            .expect("static Account schema"),
    ])
    .expect("static Account databases are unique")
}

pub fn account_indexed_identity(
    identity: &InstanceIdentity,
    account_id: &AccountId,
    producer_incarnation: u64,
) -> IndexedViewIdentity {
    IndexedViewIdentity::new(
        identity.workspace_id.to_string(),
        identity.launch_id().map(ToString::to_string),
        identity.instance_id().map(ToString::to_string),
        "Account",
        format!("account-{account_id}"),
        ACCOUNT_RESOURCE_EPOCH,
        producer_incarnation,
        account_indexed_schema_set(),
    )
    .expect("validated Account runtime identity produces a valid indexed-view identity")
}

pub fn account_indexed_environment_path(
    root: impl AsRef<Path>,
    identity: &InstanceIdentity,
    account_id: &AccountId,
) -> ContractResult<PathBuf> {
    environment_path(root, &account_indexed_identity(identity, account_id, 1))
        .map_err(|error| ContractError::Transport(error.to_string()))
}

pub fn account_indexed_key(parts: &[&str]) -> ContractResult<Vec<u8>> {
    if parts.is_empty() {
        return Err(ContractError::Invalid(
            "Account indexed key requires at least one component".into(),
        ));
    }
    let mut key = vec![KEY_VERSION];
    for part in parts {
        if part.is_empty() || part.trim() != *part || part.as_bytes().contains(&0) {
            return Err(ContractError::Invalid(
                "Account indexed key components must be non-empty and trimmed".into(),
            ));
        }
        let length: u16 = part.len().try_into().map_err(|_| {
            ContractError::Invalid("Account indexed key component is too long".into())
        })?;
        key.extend_from_slice(&length.to_be_bytes());
        key.extend_from_slice(part.as_bytes());
    }
    Ok(key)
}

pub struct AccountIndexedView {
    reader: IndexedViewReader,
    account_id: AccountId,
}

impl AccountIndexedView {
    pub fn open(
        root: impl AsRef<Path>,
        identity: &InstanceIdentity,
        account_id: AccountId,
    ) -> ContractResult<Self> {
        let path = account_indexed_environment_path(root, identity, &account_id)?;
        let options = EnvironmentOptions::new(path, ACCOUNT_MAP_SIZE)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        let reader =
            IndexedViewReader::open(&options, account_indexed_identity(identity, &account_id, 1))
                .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { reader, account_id })
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
                "Account indexed current view is not ready".into(),
            ));
        }
        Ok(metadata)
    }

    pub fn snapshot(&self) -> ContractResult<AccountIndexedSnapshot> {
        let databases = [
            ACCOUNT_SEGMENTS_DATABASE,
            ACCOUNT_BALANCES_DATABASE,
            ACCOUNT_COLLATERAL_DATABASE,
            ACCOUNT_POSITIONS_DATABASE,
            ACCOUNT_VALUATIONS_DATABASE,
            ACCOUNT_EARN_HOLDINGS_DATABASE,
            ACCOUNT_OBSERVED_ORDERS_DATABASE,
        ];
        let requests = databases.map(|database| PrefixRequest {
            database,
            prefix: &ALL_VALUES_PREFIX,
            limit: MAX_ACCOUNT_INDEXED_VALUES_PER_DATABASE + 1,
        });
        let snapshot = self
            .reader
            .snapshot(&requests)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        if snapshot.metadata.rebuild_state != kairos_indexed_view::RebuildState::Ready {
            return Err(ContractError::Transport(
                "Account indexed current view is not ready".into(),
            ));
        }
        ensure_bounded_rows(&snapshot.rows)?;
        Ok(AccountIndexedSnapshot {
            metadata: snapshot.metadata,
            account_id: self.account_id.clone(),
            rows: snapshot.rows,
        })
    }

    pub fn segments(&self) -> ContractResult<Vec<AccountIndexedViewValue>> {
        self.values(ACCOUNT_SEGMENTS_DATABASE)
    }

    pub fn balances(&self) -> ContractResult<Vec<AccountIndexedViewValue>> {
        self.values(ACCOUNT_BALANCES_DATABASE)
    }

    pub fn collateral(&self) -> ContractResult<Vec<AccountIndexedViewValue>> {
        self.values(ACCOUNT_COLLATERAL_DATABASE)
    }

    pub fn positions(&self) -> ContractResult<Vec<AccountIndexedViewValue>> {
        self.values(ACCOUNT_POSITIONS_DATABASE)
    }

    pub fn valuations(&self) -> ContractResult<Vec<AccountIndexedViewValue>> {
        self.values(ACCOUNT_VALUATIONS_DATABASE)
    }

    pub fn earn_holdings(&self) -> ContractResult<Vec<AccountIndexedViewValue>> {
        self.values(ACCOUNT_EARN_HOLDINGS_DATABASE)
    }

    pub fn observed_orders(&self) -> ContractResult<Vec<AccountIndexedViewValue>> {
        self.values(ACCOUNT_OBSERVED_ORDERS_DATABASE)
    }

    fn values(&self, database: &str) -> ContractResult<Vec<AccountIndexedViewValue>> {
        self.ensure_ready()?;
        let rows = self
            .reader
            .prefix(
                database,
                &ALL_VALUES_PREFIX,
                MAX_ACCOUNT_INDEXED_VALUES_PER_DATABASE + 1,
            )
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        if rows.len() > MAX_ACCOUNT_INDEXED_VALUES_PER_DATABASE {
            return Err(ContractError::Invalid(format!(
                "Account indexed database `{database}` exceeds its read bound"
            )));
        }
        Ok(rows
            .into_iter()
            .map(|(key, bytes)| AccountIndexedViewValue {
                account_id: self.account_id.clone(),
                key,
                bytes,
            })
            .collect())
    }
}

fn ensure_bounded_rows(rows: &BTreeMap<String, Vec<(Vec<u8>, Vec<u8>)>>) -> ContractResult<()> {
    for (database, values) in rows {
        if values.len() > MAX_ACCOUNT_INDEXED_VALUES_PER_DATABASE {
            return Err(ContractError::Invalid(format!(
                "Account indexed database `{database}` exceeds its read bound"
            )));
        }
    }
    Ok(())
}

pub struct AccountIndexedSnapshot {
    metadata: MetadataSnapshot,
    account_id: AccountId,
    rows: BTreeMap<String, Vec<(Vec<u8>, Vec<u8>)>>,
}

impl AccountIndexedSnapshot {
    pub fn metadata(&self) -> &MetadataSnapshot {
        &self.metadata
    }

    pub fn segments(&self) -> Vec<AccountIndexedViewValue> {
        self.values(ACCOUNT_SEGMENTS_DATABASE)
    }

    pub fn balances(&self) -> Vec<AccountIndexedViewValue> {
        self.values(ACCOUNT_BALANCES_DATABASE)
    }

    pub fn collateral(&self) -> Vec<AccountIndexedViewValue> {
        self.values(ACCOUNT_COLLATERAL_DATABASE)
    }

    pub fn positions(&self) -> Vec<AccountIndexedViewValue> {
        self.values(ACCOUNT_POSITIONS_DATABASE)
    }

    pub fn valuations(&self) -> Vec<AccountIndexedViewValue> {
        self.values(ACCOUNT_VALUATIONS_DATABASE)
    }

    pub fn earn_holdings(&self) -> Vec<AccountIndexedViewValue> {
        self.values(ACCOUNT_EARN_HOLDINGS_DATABASE)
    }

    pub fn observed_orders(&self) -> Vec<AccountIndexedViewValue> {
        self.values(ACCOUNT_OBSERVED_ORDERS_DATABASE)
    }

    fn values(&self, database: &str) -> Vec<AccountIndexedViewValue> {
        self.rows
            .get(database)
            .into_iter()
            .flatten()
            .map(|(key, bytes)| AccountIndexedViewValue {
                account_id: self.account_id.clone(),
                key: key.clone(),
                bytes: bytes.clone(),
            })
            .collect()
    }
}

pub struct AccountIndexedViewValue {
    account_id: AccountId,
    key: Vec<u8>,
    bytes: Vec<u8>,
}

impl AccountIndexedViewValue {
    pub fn key_components(&self) -> ContractResult<Vec<&str>> {
        decode_key_components(&self.key)
    }

    pub fn segment(&self) -> ContractResult<fb::AccountSegmentCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::account_segment_current_buffer_has_identifier,
            fb::root_as_account_segment_current,
            "ASG3 AccountSegmentCurrent",
        )?;
        self.validate(&[value.state().segment_key()], value.account_id())?;
        Ok(value)
    }

    pub fn balance(&self) -> ContractResult<fb::AccountBalanceCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::account_balance_current_buffer_has_identifier,
            fb::root_as_account_balance_current,
            "ABA3 AccountBalanceCurrent",
        )?;
        self.validate(
            &[value.segment_key(), value.balance().asset_id()],
            value.account_id(),
        )?;
        Ok(value)
    }

    pub fn collateral(&self) -> ContractResult<fb::AccountCollateralCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::account_collateral_current_buffer_has_identifier,
            fb::root_as_account_collateral_current,
            "ACO3 AccountCollateralCurrent",
        )?;
        self.validate(
            &[value.segment_key(), value.balance().asset_id()],
            value.account_id(),
        )?;
        Ok(value)
    }

    pub fn position(&self) -> ContractResult<fb::AccountPositionCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::account_position_current_buffer_has_identifier,
            fb::root_as_account_position_current,
            "APO3 AccountPositionCurrent",
        )?;
        self.validate(
            &[
                value.segment_key(),
                value.position().instrument_id(),
                value
                    .position()
                    .position_side()
                    .variant_name()
                    .unwrap_or("UNSPECIFIED"),
            ],
            value.account_id(),
        )?;
        Ok(value)
    }

    pub fn valuation(&self) -> ContractResult<fb::AccountValuationCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::account_valuation_current_buffer_has_identifier,
            fb::root_as_account_valuation_current,
            "AVL3 AccountValuationCurrent",
        )?;
        self.validate(&[value.segment_key()], value.account_id())?;
        Ok(value)
    }

    pub fn earn_holding(&self) -> ContractResult<fb::AccountEarnHoldingCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::account_earn_holding_current_buffer_has_identifier,
            fb::root_as_account_earn_holding_current,
            "AEH3 AccountEarnHoldingCurrent",
        )?;
        self.validate(
            &[value.segment_key(), value.holding().holding_key()],
            value.account_id(),
        )?;
        Ok(value)
    }

    pub fn observed_order(&self) -> ContractResult<fb::AccountObservedOrderCurrent<'_>> {
        let value = decode(
            &self.bytes,
            fb::account_observed_order_current_buffer_has_identifier,
            fb::root_as_account_observed_order_current,
            "AOO3 AccountObservedOrderCurrent",
        )?;
        self.validate(
            &[
                value.segment_key(),
                value.order().source_id(),
                value.order().observation_id(),
            ],
            value.account_id(),
        )?;
        Ok(value)
    }

    fn validate(&self, expected_parts: &[&str], account_id: &str) -> ContractResult<()> {
        if account_id != self.account_id.as_str() || self.key_components()? != expected_parts {
            return Err(ContractError::Invalid(
                "Account indexed key/value semantic identity mismatch".into(),
            ));
        }
        Ok(())
    }
}

fn decode_key_components(key: &[u8]) -> ContractResult<Vec<&str>> {
    if key.first() != Some(&KEY_VERSION) {
        return Err(ContractError::Invalid(
            "invalid Account indexed key version".into(),
        ));
    }
    let mut input = &key[1..];
    let mut parts = Vec::new();
    while !input.is_empty() {
        if input.len() < 2 {
            return Err(ContractError::Invalid(
                "invalid Account indexed key length".into(),
            ));
        }
        let length = usize::from(u16::from_be_bytes([input[0], input[1]]));
        input = &input[2..];
        if input.len() < length {
            return Err(ContractError::Invalid(
                "invalid Account indexed key component".into(),
            ));
        }
        let (part, remainder) = input.split_at(length);
        parts.push(std::str::from_utf8(part).map_err(|error| {
            ContractError::Invalid(format!("invalid Account indexed key UTF-8: {error}"))
        })?);
        input = remainder;
    }
    if parts.is_empty() {
        return Err(ContractError::Invalid(
            "Account indexed key has no components".into(),
        ));
    }
    Ok(parts)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn composite_keys_are_versioned_and_unambiguous() {
        assert_eq!(
            account_indexed_key(&["spot", "USDT"]).unwrap(),
            b"\x01\x00\x04spot\x00\x04USDT"
        );
        assert_eq!(
            decode_key_components(b"\x01\x00\x04spot\x00\x04USDT").unwrap(),
            vec!["spot", "USDT"]
        );
    }
}
