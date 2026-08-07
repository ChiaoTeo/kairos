use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::{
    account::v_1 as account_fb,
    common::v_1::{Decimal64, SnapshotHeader, SnapshotHeaderArgs},
};

use crate::application::account::AccountsSnapshot;
use crate::domain::DecimalValue;

pub struct FlatbuffersAccountPublisher {
    pub owner_actor_id: String,
    pub last_payload: Option<Vec<u8>>,
}

pub struct FileAccountPublisher {
    pub path: std::path::PathBuf,
    pub inner: FlatbuffersAccountPublisher,
}

impl FileAccountPublisher {
    pub fn new(path: impl Into<std::path::PathBuf>, owner_actor_id: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            inner: FlatbuffersAccountPublisher::new(owner_actor_id),
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
        Self {
            owner_actor_id: owner_actor_id.into(),
            last_payload: None,
        }
    }
}

impl FlatbuffersAccountPublisher {
    pub fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        let mut builder = FlatBufferBuilder::new();
        let mut account_offsets = Vec::new();
        for view in &snapshot.accounts {
            let account = &view.account;
            let mut balance_offsets = Vec::new();
            for balance in account.state.balances.values() {
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
            for balance in account.state.collateral.values() {
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
            for position in account.state.positions.values() {
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
            for order in account.state.open_orders.values() {
                let order_id = builder.create_string(&order.order_id);
                let venue_order_id = order
                    .venue_order_id
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
                        venue_order_id,
                        instrument_id: Some(instrument_id),
                        side: Some(side),
                        quantity: Some(&quantity),
                        status: Some(status),
                        filled_quantity: Some(&filled_quantity),
                    },
                ));
            }
            let open_orders = builder.create_vector(&open_order_offsets);
            let account_id = builder.create_string(&account.segment.identity.account_id);
            let segment_key = builder.create_string(&account.segment.segment_key);
            let environment = builder.create_string(&account.segment.environment);
            let broker = builder.create_string(&account.segment.identity.broker);
            let status = builder.create_string(account.state.status.as_str());
            let model = account
                .segment
                .account_model
                .as_ref()
                .map(|value| builder.create_string(value));
            let margin_mode = account.state.margin_mode.map(|value| {
                builder.create_string(match value {
                    crate::domain::MarginMode::Cross => "cross",
                    crate::domain::MarginMode::Isolated => "isolated",
                })
            });
            let position_mode = account.state.position_mode.map(|value| {
                builder.create_string(match value {
                    crate::domain::PositionMode::OneWay => "one_way",
                    crate::domain::PositionMode::Hedge => "hedge",
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
                    stale: account.state.stale,
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
                    .filter(|v| v.account.state.status.as_str() == "ready")
                    .count() as u64,
                accounts: Some(accounts),
            },
        );
        let snapshot_id = builder.create_string(&format!("account-{}", snapshot.event_sequence));
        let view_key = builder.create_string("account.current");
        let owner = builder.create_string(&self.owner_actor_id);
        let stream = builder.create_string("account.events");
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(owner),
                event_stream_id: Some(stream),
                workspace_id: None,
                launch_id: None,
                instance_id: None,
                event_sequence: snapshot.event_sequence,
                version: 1,
                generation: snapshot.generation,
                generated_at_unix_nanos: 0,
                as_of_unix_nanos: 0,
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

fn decimal(value: DecimalValue) -> Decimal64 {
    Decimal64::new(value.mantissa, value.scale)
}
