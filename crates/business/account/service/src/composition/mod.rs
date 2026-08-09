//! Composition helpers for account sources and process-owned snapshot writers.

pub mod account;

use crate::application::AccountsSnapshot;
use crate::domain::{AccountModel, AccountStatus, MarginMode, PositionMode};
use kairos_account_contract::model as contract;

pub struct FlatbuffersAccountPublisher {
    pub owner_actor_id: String,
    pub identity: kairos_protocol::InstanceIdentity,
    pub last_payload: Option<Vec<u8>>,
    inner: kairos_account_contract::encoding::FlatbuffersAccountPublisher,
}

impl FlatbuffersAccountPublisher {
    pub fn new(owner_actor_id: impl Into<String>) -> Self {
        Self::new_with_identity(owner_actor_id, kairos_protocol::InstanceIdentity::default())
    }

    pub fn new_with_identity(
        owner_actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Self {
        let owner_actor_id = owner_actor_id.into();
        Self {
            inner:
                kairos_account_contract::encoding::FlatbuffersAccountPublisher::new_with_identity(
                    owner_actor_id.clone(),
                    identity.clone(),
                ),
            owner_actor_id,
            identity,
            last_payload: None,
        }
    }

    pub fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        let snapshot = to_contract_snapshot(snapshot);
        self.inner.publish(&snapshot)?;
        self.last_payload = self.inner.last_payload.clone();
        Ok(())
    }
}

pub struct FileAccountPublisher {
    pub path: std::path::PathBuf,
    pub inner: FlatbuffersAccountPublisher,
}

pub struct MmapAccountPublisher {
    inner: kairos_account_contract::encoding::MmapAccountPublisher,
}

impl MmapAccountPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        owner_actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_account_contract::encoding::MmapAccountPublisher::create(
                path,
                slot_size,
                owner_actor_id,
                identity,
            )
            .map_err(|error| error.to_string())?,
        })
    }

    pub fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        let snapshot = to_contract_snapshot(snapshot);
        self.inner
            .publish(&snapshot)
            .map_err(|error| error.to_string())
    }
}

/// Translate the private application projection into the public wire model
/// without routing hot-path publication through serde JSON.
fn to_contract_snapshot(snapshot: &AccountsSnapshot) -> contract::AccountsSnapshot {
    contract::AccountsSnapshot {
        actor_id: snapshot.actor_id.clone(),
        generation: snapshot.generation,
        event_sequence: snapshot.event_sequence,
        accounts: snapshot
            .accounts
            .iter()
            .map(|account| contract::AccountProjection {
                account_id: account.account_id.clone(),
                segment_key: account.segment_key.clone(),
                environment: account.environment.clone(),
                broker: account.broker.clone(),
                configured_account_model: account.configured_account_model.clone(),
                observed_account_model: account.observed_account_model.map(account_model),
                status: account_status(account.status),
                stale: account.stale,
                observed_at_unix_nanos: account.observed_at_unix_nanos,
                generation: account.generation,
                event_sequence: account.event_sequence,
                equity: account.equity.map(decimal),
                initial_equity: account.initial_equity.map(decimal),
                net_profit: account.net_profit.map(decimal),
                margin_mode: account.margin_mode.map(margin_mode),
                position_mode: account.position_mode.map(position_mode),
                balances: account.balances.iter().map(balance).collect(),
                collateral: account.collateral.iter().map(balance).collect(),
                positions: account.positions.iter().map(position).collect(),
                open_orders: account.open_orders.iter().map(open_order).collect(),
            })
            .collect(),
    }
}

fn decimal(value: crate::domain::Decimal) -> contract::Decimal {
    contract::Decimal {
        mantissa: value.mantissa,
        scale: value.scale,
    }
}

fn account_model(value: AccountModel) -> contract::AccountModel {
    match value {
        AccountModel::NoMargin => contract::AccountModel::NoMargin,
        AccountModel::Margin => contract::AccountModel::Margin,
        AccountModel::Contract => contract::AccountModel::Contract,
        AccountModel::ContractUnified => contract::AccountModel::ContractUnified,
        AccountModel::Unified => contract::AccountModel::Unified,
        AccountModel::PortfolioMargin => contract::AccountModel::PortfolioMargin,
    }
}

fn account_status(value: AccountStatus) -> contract::AccountStatus {
    match value {
        AccountStatus::Unknown => contract::AccountStatus::Unknown,
        AccountStatus::Ready => contract::AccountStatus::Ready,
        AccountStatus::Reconciling => contract::AccountStatus::Reconciling,
        AccountStatus::TypeMismatch => contract::AccountStatus::TypeMismatch,
        AccountStatus::Suspended => contract::AccountStatus::Suspended,
        AccountStatus::Unavailable => contract::AccountStatus::Unavailable,
    }
}

fn margin_mode(value: MarginMode) -> contract::MarginMode {
    match value {
        MarginMode::Cross => contract::MarginMode::Cross,
        MarginMode::Isolated => contract::MarginMode::Isolated,
    }
}

fn position_mode(value: PositionMode) -> contract::PositionMode {
    match value {
        PositionMode::OneWay => contract::PositionMode::OneWay,
        PositionMode::Hedge => contract::PositionMode::Hedge,
    }
}

fn balance(value: &crate::domain::Balance) -> contract::Balance {
    contract::Balance {
        asset_id: value.asset_id.to_string(),
        asset_code: value.asset_code.clone(),
        total: decimal(value.total),
        available: value.available.map(decimal),
        locked: value.locked.map(decimal),
        borrowed: value.borrowed.map(decimal),
        interest: value.interest.map(decimal),
    }
}

fn position(value: &crate::domain::Position) -> contract::Position {
    contract::Position {
        instrument_id: value.instrument_id.to_string(),
        market_id: value.market_id.clone(),
        quantity: decimal(value.quantity),
        average_price: value.average_price.map(decimal),
        mark_price: value.mark_price.map(decimal),
        unrealized_pnl: value.unrealized_pnl.map(decimal),
        realized_pnl: value.realized_pnl.map(decimal),
        updated_at_unix_nanos: value.updated_at_unix_nanos,
    }
}

fn open_order(value: &crate::domain::OpenOrder) -> contract::OpenOrder {
    contract::OpenOrder {
        order_id: value.order_id.clone(),
        venue_order_id: value.venue_order_id.clone(),
        instrument_id: value.instrument_id.to_string(),
        side: value.side.clone(),
        quantity: decimal(value.quantity),
        filled_quantity: decimal(value.filled_quantity),
        status: value.status.clone(),
    }
}

impl FileAccountPublisher {
    pub fn new(path: impl Into<std::path::PathBuf>, owner_actor_id: impl Into<String>) -> Self {
        Self::new_with_identity(
            path,
            owner_actor_id,
            kairos_protocol::InstanceIdentity::default(),
        )
    }

    pub fn new_with_identity(
        path: impl Into<std::path::PathBuf>,
        owner_actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Self {
        Self {
            path: path.into(),
            inner: FlatbuffersAccountPublisher::new_with_identity(owner_actor_id, identity),
        }
    }

    pub fn publish(
        &mut self,
        snapshot: &crate::application::AccountsSnapshot,
    ) -> Result<(), String> {
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

use crate::domain::{AccountSnapshot, SegmentKey};

pub fn empty_snapshot(segment_key: impl Into<String>) -> AccountSnapshot {
    AccountSnapshot {
        segment_key: SegmentKey::new(segment_key.into()).expect("snapshot segment is required"),
        balances: Vec::new(),
        collateral: Vec::new(),
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: AccountStatus::Ready,
        observed_at_unix_nanos: 0,
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        kind: crate::domain::SnapshotKind::Full,
    }
}
