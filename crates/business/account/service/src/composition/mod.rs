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

pub struct AeronAccountEventPublisher {
    inner: kairos_account_contract::account_event::AeronAccountEventPublisher,
}

impl AeronAccountEventPublisher {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        owner_actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_account_contract::account_event::AeronAccountEventPublisher::connect(
                aeron_dir,
                channel,
                stream_id,
                owner_actor_id,
                identity,
            )?,
        })
    }
}

impl crate::application::AccountEventPublisher for AeronAccountEventPublisher {
    fn publish(&mut self, event: &crate::application::AccountBusinessEvent) -> Result<(), String> {
        self.inner.publish(&to_contract_event(event))
    }
}

fn to_contract_event(
    event: &crate::application::AccountBusinessEvent,
) -> kairos_account_contract::account_event::AccountStrategyEvent {
    use crate::application::AccountBusinessChange;
    use kairos_account_contract::account_event::AccountStrategyChange;

    kairos_account_contract::account_event::AccountStrategyEvent {
        sequence: event.sequence.get(),
        account_id: event.account_id.to_string(),
        occurred_at_unix_nanos: event.occurred_at_unix_nanos.get(),
        changes: event
            .changes
            .iter()
            .map(|change| match change {
                AccountBusinessChange::Balance { segment_key, value } => {
                    AccountStrategyChange::Balance {
                        segment_key: segment_key.to_string(),
                        value: contract::Balance {
                            asset_id: value.asset_id.to_string(),
                            asset_code: value.asset_code.to_string(),
                            total: decimal_parts(value.total.mantissa(), value.total.scale()),
                            available: value
                                .available
                                .map(|v| decimal_parts(v.mantissa(), v.scale())),
                            locked: value.locked.map(|v| decimal_parts(v.mantissa(), v.scale())),
                            borrowed: value
                                .borrowed
                                .map(|v| decimal_parts(v.mantissa(), v.scale())),
                            interest: value
                                .interest
                                .map(|v| decimal_parts(v.mantissa(), v.scale())),
                        },
                    }
                }
                AccountBusinessChange::Position { segment_key, value } => {
                    AccountStrategyChange::Position {
                        segment_key: segment_key.to_string(),
                        value: contract::Position {
                            instrument_id: value.instrument_id.to_string(),
                            market_id: value.market_id.as_ref().map(ToString::to_string),
                            quantity: decimal_parts(
                                value.quantity.mantissa(),
                                value.quantity.scale(),
                            ),
                            average_price: value
                                .average_price
                                .map(|v| decimal_parts(v.mantissa(), v.scale())),
                            mark_price: value
                                .mark_price
                                .map(|v| decimal_parts(v.mantissa(), v.scale())),
                            unrealized_pnl: value
                                .unrealized_pnl
                                .map(|v| decimal_parts(v.mantissa(), v.scale())),
                            realized_pnl: value
                                .realized_pnl
                                .map(|v| decimal_parts(v.mantissa(), v.scale())),
                            updated_at_unix_nanos: value.updated_at_unix_nanos.get(),
                        },
                    }
                }
                AccountBusinessChange::Equity { segment_key, value } => {
                    AccountStrategyChange::Equity {
                        segment_key: segment_key.to_string(),
                        value: value.map(|v| decimal_parts(v.mantissa(), v.scale())),
                    }
                }
                AccountBusinessChange::Status {
                    segment_key,
                    status,
                    stale,
                } => AccountStrategyChange::Status {
                    segment_key: segment_key.to_string(),
                    status: account_status(*status),
                    stale: *stale,
                },
            })
            .collect(),
    }
}

impl crate::application::AccountSnapshotPublisher for MmapAccountPublisher {
    fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        MmapAccountPublisher::publish(self, snapshot)
    }
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
        actor_id: snapshot.actor_id.to_string(),
        generation: snapshot.generation.get(),
        accounts: snapshot
            .accounts
            .iter()
            .map(|account| contract::AccountProjection {
                account_id: account.account_id.to_string(),
                segment_key: account.segment_key.to_string(),
                environment: account.environment.clone(),
                broker: account.broker.clone(),
                configured_account_model: account.configured_account_model.clone(),
                observed_account_model: account.observed_account_model.map(account_model),
                status: account_status(account.status),
                stale: account.stale,
                observed_at_unix_nanos: account.observed_at_unix_nanos.get(),
                generation: account.generation.get(),
                equity: account
                    .equity
                    .map(|value| decimal_parts(value.mantissa(), value.scale())),
                initial_equity: account
                    .initial_equity
                    .map(|value| decimal_parts(value.mantissa(), value.scale())),
                net_profit: account
                    .net_profit
                    .map(|value| decimal_parts(value.mantissa(), value.scale())),
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

fn decimal_parts(mantissa: i64, scale: u8) -> contract::Decimal {
    contract::Decimal { mantissa, scale }
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
        asset_code: value.asset_code.to_string(),
        total: decimal_parts(value.total.mantissa(), value.total.scale()),
        available: value
            .available
            .map(|value| decimal_parts(value.mantissa(), value.scale())),
        locked: value
            .locked
            .map(|value| decimal_parts(value.mantissa(), value.scale())),
        borrowed: value
            .borrowed
            .map(|value| decimal_parts(value.mantissa(), value.scale())),
        interest: value
            .interest
            .map(|value| decimal_parts(value.mantissa(), value.scale())),
    }
}

fn position(value: &crate::domain::Position) -> contract::Position {
    contract::Position {
        instrument_id: value.instrument_id.to_string(),
        market_id: value.market_id.clone().map(|value| value.to_string()),
        quantity: decimal_parts(value.quantity.mantissa(), value.quantity.scale()),
        average_price: value
            .average_price
            .map(|value| decimal_parts(value.mantissa(), value.scale())),
        mark_price: value
            .mark_price
            .map(|value| decimal_parts(value.mantissa(), value.scale())),
        unrealized_pnl: value
            .unrealized_pnl
            .map(|value| decimal_parts(value.mantissa(), value.scale())),
        realized_pnl: value
            .realized_pnl
            .map(|value| decimal_parts(value.mantissa(), value.scale())),
        updated_at_unix_nanos: value.updated_at_unix_nanos.get(),
    }
}

fn open_order(value: &crate::domain::OpenOrder) -> contract::OpenOrder {
    contract::OpenOrder {
        order_id: value.order_id.to_string(),
        remote_order_id: value.remote_order_id.as_ref().map(ToString::to_string),
        instrument_id: value.instrument_id.to_string(),
        side: match value.side {
            kairos_domain_types::OrderSide::Buy => "buy",
            kairos_domain_types::OrderSide::Sell => "sell",
        }
        .into(),
        quantity: decimal_parts(value.quantity.mantissa(), value.quantity.scale()),
        filled_quantity: decimal_parts(
            value.filled_quantity.mantissa(),
            value.filled_quantity.scale(),
        ),
        status: match value.status {
            kairos_domain_types::OrderStatus::Pending => "pending",
            kairos_domain_types::OrderStatus::Acknowledged => "acknowledged",
            kairos_domain_types::OrderStatus::Accepted => "accepted",
            kairos_domain_types::OrderStatus::PartiallyFilled => "partially_filled",
            kairos_domain_types::OrderStatus::Filled => "filled",
            kairos_domain_types::OrderStatus::Canceled => "canceled",
            kairos_domain_types::OrderStatus::Rejected => "rejected",
            kairos_domain_types::OrderStatus::Expired => "expired",
            kairos_domain_types::OrderStatus::Unknown => "unknown",
        }
        .into(),
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
        observed_at_unix_nanos: 0.into(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        kind: crate::domain::SnapshotKind::Full,
    }
}
