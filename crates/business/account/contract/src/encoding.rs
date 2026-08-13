use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::{
    account::v_1 as account_fb,
    common::v_1::{Decimal64, SnapshotHeader, SnapshotHeaderArgs},
};
use kairos_protocol::InstanceIdentity;

use crate::model::{AccountsSnapshot, Decimal, MarginMode, PositionMode};
use crate::transport::MmapSnapshotPublisher;
use crate::{ContractResult, SnapshotEnvelope};

pub struct FlatbuffersAccountPublisher {
    pub owner_actor_id: String,
    pub identity: InstanceIdentity,
    pub last_payload: Option<Vec<u8>>,
}

pub struct FileAccountPublisher {
    pub path: std::path::PathBuf,
    pub inner: FlatbuffersAccountPublisher,
}

/// Publishes the encoded Account snapshot through the stable mmap contract.
pub struct MmapAccountPublisher {
    publisher: MmapSnapshotPublisher,
    encoder: FlatbuffersAccountPublisher,
}

impl MmapAccountPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        owner_actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> ContractResult<Self> {
        let owner_actor_id = owner_actor_id.into();
        Ok(Self {
            publisher: MmapSnapshotPublisher::create(path, slot_size)?,
            encoder: FlatbuffersAccountPublisher::new_with_identity(owner_actor_id, identity),
        })
    }

    pub fn publish(&mut self, snapshot: &AccountsSnapshot) -> ContractResult<()> {
        self.encoder
            .publish(snapshot)
            .map_err(crate::ContractError::Invalid)?;
        self.publisher.publish(&SnapshotEnvelope {
            view_key: "account.current".into(),
            producer_id: self.encoder.owner_actor_id.clone(),
            generation: snapshot.generation,
            published_at_unix_nanos: 0,
            payload: self.encoder.last_payload.clone().unwrap_or_default(),
        })
    }
}

impl FileAccountPublisher {
    pub fn new(path: impl Into<std::path::PathBuf>, owner_actor_id: impl Into<String>) -> Self {
        Self::new_with_identity(path, owner_actor_id, InstanceIdentity::default())
    }

    pub fn new_with_identity(
        path: impl Into<std::path::PathBuf>,
        owner_actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Self {
        Self {
            path: path.into(),
            inner: FlatbuffersAccountPublisher::new_with_identity(owner_actor_id, identity),
        }
    }
}

impl FileAccountPublisher {
    pub fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        self.inner.publish(snapshot)?;
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let temporary = self.path.with_extension("tmp");
        std::fs::write(
            &temporary,
            self.inner.last_payload.as_deref().unwrap_or_default(),
        )
        .map_err(|error| error.to_string())?;
        std::fs::rename(temporary, &self.path).map_err(|error| error.to_string())
    }
}

impl FlatbuffersAccountPublisher {
    pub fn new(owner_actor_id: impl Into<String>) -> Self {
        Self::new_with_identity(owner_actor_id, InstanceIdentity::default())
    }

    pub fn new_with_identity(
        owner_actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Self {
        Self {
            owner_actor_id: owner_actor_id.into(),
            identity,
            last_payload: None,
        }
    }
}

impl FlatbuffersAccountPublisher {
    pub fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        let mut builder = FlatBufferBuilder::new();
        let mut account_offsets = Vec::new();
        for view in &snapshot.accounts {
            let account = view;
            let mut balance_offsets = Vec::new();
            for balance in &account.balances {
                let asset_id = builder.create_string(&balance.asset_id);
                let asset_code = builder.create_string(&balance.asset_code);
                let total = decimal(balance.total);
                let value = account_fb::Balance::create(
                    &mut builder,
                    &account_fb::BalanceArgs {
                        asset_id: Some(asset_id),
                        asset_code: Some(asset_code),
                        total: Some(&total),
                        ..Default::default()
                    },
                );
                balance_offsets.push(value);
            }
            let balances = builder.create_vector(&balance_offsets);
            let mut collateral_offsets = Vec::new();
            for balance in &account.collateral {
                let asset_id = builder.create_string(&balance.asset_id);
                let asset_code = builder.create_string(&balance.asset_code);
                let total = decimal(balance.total);
                collateral_offsets.push(account_fb::Balance::create(
                    &mut builder,
                    &account_fb::BalanceArgs {
                        asset_id: Some(asset_id),
                        asset_code: Some(asset_code),
                        total: Some(&total),
                        ..Default::default()
                    },
                ));
            }
            let collateral = builder.create_vector(&collateral_offsets);
            let mut position_offsets = Vec::new();
            for position in &account.positions {
                let instrument_id = builder.create_string(&position.instrument_id);
                let quantity = decimal(position.quantity);
                let value = account_fb::Position::create(
                    &mut builder,
                    &account_fb::PositionArgs {
                        instrument_id: Some(instrument_id),
                        quantity: Some(&quantity),
                        updated_at_unix_nanos: position.updated_at_unix_nanos,
                        ..Default::default()
                    },
                );
                position_offsets.push(value);
            }
            let positions = builder.create_vector(&position_offsets);
            let mut open_order_offsets = Vec::new();
            for order in &account.open_orders {
                let order_id = builder.create_string(&order.order_id);
                let remote_order_id = order
                    .remote_order_id
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let instrument_id = builder.create_string(&order.instrument_id);
                let side = builder.create_string(&order.side);
                let quantity = decimal(order.quantity);
                let status = builder.create_string(&order.status);
                let filled_quantity = decimal(order.filled_quantity);
                open_order_offsets.push(account_fb::OpenOrder::create(
                    &mut builder,
                    &account_fb::OpenOrderArgs {
                        order_id: Some(order_id),
                        remote_order_id,
                        instrument_id: Some(instrument_id),
                        side: Some(side),
                        quantity: Some(&quantity),
                        status: Some(status),
                        filled_quantity: Some(&filled_quantity),
                    },
                ));
            }
            let open_orders = builder.create_vector(&open_order_offsets);
            let account_id = builder.create_string(&account.account_id);
            let segment_key = builder.create_string(&account.segment_key);
            let environment = builder.create_string(&account.environment);
            let broker = builder.create_string(&account.broker);
            let status = builder.create_string(account.status.as_str());
            let model = account
                .configured_account_model
                .as_ref()
                .map(|value| builder.create_string(value));
            let margin_mode = account.margin_mode.map(|value| {
                builder.create_string(match value {
                    MarginMode::Cross => "cross",
                    MarginMode::Isolated => "isolated",
                })
            });
            let position_mode = account.position_mode.map(|value| {
                builder.create_string(match value {
                    PositionMode::OneWay => "one_way",
                    PositionMode::Hedge => "hedge",
                })
            });
            let value = account_fb::Account::create(
                &mut builder,
                &account_fb::AccountArgs {
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    environment: Some(environment),
                    broker: Some(broker),
                    account_model: model,
                    status: Some(status),
                    stale: account.stale,
                    balances: Some(balances),
                    collateral: Some(collateral),
                    positions: Some(positions),
                    open_orders: Some(open_orders),
                    margin_mode,
                    position_mode,
                    ..Default::default()
                },
            );
            account_offsets.push(value);
        }
        let accounts = builder.create_vector(&account_offsets);
        let payload = account_fb::Accounts::create(
            &mut builder,
            &account_fb::AccountsArgs {
                account_count: snapshot.accounts.len() as u64,
                active_count: snapshot
                    .accounts
                    .iter()
                    .filter(|v| v.status.as_str() == "ready")
                    .count() as u64,
                accounts: Some(accounts),
            },
        );
        let snapshot_id = builder.create_string(&format!("account-{}", snapshot.generation));
        let view_key = builder.create_string("account.current");
        let owner = builder.create_string(&self.owner_actor_id);
        let workspace_id = non_empty_string(&mut builder, &self.identity.workspace_id);
        let launch_id = non_empty_string(&mut builder, &self.identity.launch_id);
        let instance_id = non_empty_string(&mut builder, &self.identity.instance_id);
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(owner),
                workspace_id,
                launch_id,
                instance_id,
                version: 1,
                generation: snapshot.generation,
                generated_at_unix_nanos: now_unix_nanos(),
                as_of_unix_nanos: account_snapshot_as_of(snapshot),
                complete: true,
            },
        );
        let root = account_fb::AccountsSnapshot::create(
            &mut builder,
            &account_fb::AccountsSnapshotArgs {
                header: Some(header),
                payload: Some(payload),
            },
        );
        account_fb::finish_accounts_snapshot_buffer(&mut builder, root);
        self.last_payload = Some(builder.finished_data().to_vec());
        Ok(())
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn account_snapshot_as_of(snapshot: &AccountsSnapshot) -> u64 {
    snapshot
        .accounts
        .iter()
        .map(|account| account.observed_at_unix_nanos)
        .max()
        .unwrap_or_default()
}

fn decimal(value: Decimal) -> Decimal64 {
    Decimal64::new(value.mantissa, value.scale)
}

fn non_empty_string<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{AccountProjection, AccountStatus};

    #[test]
    fn snapshot_header_carries_generation_and_business_as_of_time() {
        let snapshot = AccountsSnapshot {
            actor_id: "account:test".into(),
            generation: 7,
            accounts: vec![AccountProjection {
                account_id: "paper".into(),
                segment_key: "spot".into(),
                environment: "paper".into(),
                broker: "test".into(),
                configured_account_model: None,
                observed_account_model: None,
                status: AccountStatus::Ready,
                stale: false,
                observed_at_unix_nanos: 123,
                generation: 7,
                equity: None,
                initial_equity: None,
                net_profit: None,
                margin_mode: None,
                position_mode: None,
                balances: vec![],
                collateral: vec![],
                positions: vec![],
                open_orders: vec![],
            }],
        };
        let mut publisher = FlatbuffersAccountPublisher::new("account:test");

        publisher.publish(&snapshot).unwrap();
        let root =
            account_fb::root_as_accounts_snapshot(publisher.last_payload.as_deref().unwrap())
                .unwrap();
        let header = root.header();
        assert_eq!(header.version(), 1);
        assert_eq!(header.generation(), 7);
        assert_eq!(header.as_of_unix_nanos(), 123);
        assert!(header.generated_at_unix_nanos() > 0);
        assert!(header.complete());
    }
}
