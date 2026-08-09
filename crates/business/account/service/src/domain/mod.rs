use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

mod decimal;
mod error;
mod identity;
mod market_profile;
pub use decimal::Decimal;
pub use error::AccountDomainError;
pub use identity::{AccountId, AssetId, ExternalOrderId, FillId, InstrumentId, SegmentKey};
pub use market_profile::AccountMarketProfile;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ExternalAccountIdentity {
    pub broker: String,
    pub account_id: AccountId,
}

impl ExternalAccountIdentity {
    pub fn new(
        broker: impl Into<String>,
        account_id: impl Into<String>,
    ) -> Result<Self, AccountDomainError> {
        let broker = broker.into();
        if broker.trim().is_empty() {
            return Err(AccountDomainError::Required { field: "broker" });
        }
        Ok(Self {
            broker,
            account_id: AccountId::new(account_id)?,
        })
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct AccountSegment {
    pub identity: ExternalAccountIdentity,
    pub segment_key: SegmentKey,
    pub environment: String,
    pub account_model: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AccountModel {
    NoMargin,
    Margin,
    Contract,
    ContractUnified,
    Unified,
    PortfolioMargin,
}

impl AccountModel {
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "no_margin" | "spot" => Some(Self::NoMargin),
            "margin" | "cross_margin" | "isolated_margin" => Some(Self::Margin),
            "contract" | "futures" | "swap" => Some(Self::Contract),
            "contract_unified" => Some(Self::ContractUnified),
            "unified" | "multi_currency_margin" => Some(Self::Unified),
            "portfolio_margin" => Some(Self::PortfolioMargin),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MarginMode {
    Cross,
    Isolated,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum PositionMode {
    OneWay,
    Hedge,
}

impl AccountSegment {
    pub fn validate(&self) -> Result<(), AccountDomainError> {
        if self.environment.trim().is_empty() {
            return Err(AccountDomainError::Required {
                field: "environment",
            });
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Balance {
    pub asset_id: AssetId,
    pub asset_code: String,
    pub total: Decimal,
    pub available: Option<Decimal>,
    pub locked: Option<Decimal>,
    pub borrowed: Option<Decimal>,
    pub interest: Option<Decimal>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Position {
    pub instrument_id: InstrumentId,
    pub market_id: Option<String>,
    pub quantity: Decimal,
    pub average_price: Option<Decimal>,
    pub mark_price: Option<Decimal>,
    pub unrealized_pnl: Option<Decimal>,
    pub realized_pnl: Option<Decimal>,
    pub updated_at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AccountStatus {
    Unknown,
    Ready,
    Reconciling,
    TypeMismatch,
    Suspended,
    Unavailable,
}

impl Default for AccountStatus {
    fn default() -> Self {
        Self::Unknown
    }
}

impl AccountStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::Ready => "ready",
            Self::Reconciling => "reconciling",
            Self::TypeMismatch => "type_mismatch",
            Self::Suspended => "suspended",
            Self::Unavailable => "unavailable",
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountState {
    balances: BTreeMap<AssetId, Balance>,
    collateral: BTreeMap<AssetId, Balance>,
    positions: BTreeMap<InstrumentId, Position>,
    open_orders: BTreeMap<String, OpenOrder>,
    status: AccountStatus,
    stale: bool,
    observed_at_unix_nanos: u64,
    generation: u64,
    event_sequence: u64,
    equity: Option<Decimal>,
    initial_equity: Option<Decimal>,
    net_profit: Option<Decimal>,
    observed_account_model: Option<AccountModel>,
    margin_mode: Option<MarginMode>,
    position_mode: Option<PositionMode>,
    #[serde(default)]
    snapshot_watermark_unix_nanos: u64,
    #[serde(default)]
    fill_watermark_unix_nanos: u64,
    #[serde(default)]
    order_watermarks_unix_nanos: BTreeMap<String, u64>,
    #[serde(default)]
    applied_fill_ids: std::collections::BTreeSet<FillId>,
    #[serde(default)]
    fills: BTreeMap<FillId, AccountFill>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Account {
    segment: AccountSegment,
    state: AccountState,
}

impl AccountState {
    pub fn balances(&self) -> &BTreeMap<AssetId, Balance> {
        &self.balances
    }

    pub fn collateral(&self) -> &BTreeMap<AssetId, Balance> {
        &self.collateral
    }

    pub fn positions(&self) -> &BTreeMap<InstrumentId, Position> {
        &self.positions
    }

    pub fn open_orders(&self) -> &BTreeMap<String, OpenOrder> {
        &self.open_orders
    }

    pub fn status(&self) -> AccountStatus {
        self.status
    }

    pub fn stale(&self) -> bool {
        self.stale
    }

    pub fn observed_at_unix_nanos(&self) -> u64 {
        self.observed_at_unix_nanos
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn event_sequence(&self) -> u64 {
        self.event_sequence
    }

    pub fn equity(&self) -> Option<Decimal> {
        self.equity
    }

    pub fn initial_equity(&self) -> Option<Decimal> {
        self.initial_equity
    }

    pub fn net_profit(&self) -> Option<Decimal> {
        self.net_profit
    }

    pub fn observed_account_model(&self) -> Option<AccountModel> {
        self.observed_account_model
    }

    pub fn margin_mode(&self) -> Option<MarginMode> {
        self.margin_mode
    }

    pub fn position_mode(&self) -> Option<PositionMode> {
        self.position_mode
    }
}

impl Account {
    pub fn new(segment: AccountSegment) -> Result<Self, AccountDomainError> {
        segment.validate()?;
        Ok(Self {
            segment,
            state: AccountState::default(),
        })
    }

    pub fn segment(&self) -> &AccountSegment {
        &self.segment
    }

    pub fn state(&self) -> &AccountState {
        &self.state
    }

    pub(crate) fn restore_state(&mut self, state: AccountState) {
        self.state = state;
    }

    pub fn begin_reconciliation(&mut self) -> ApplyOutcome {
        if self.state.status == AccountStatus::Reconciling {
            return ApplyOutcome::NoChange;
        }
        self.state.status = AccountStatus::Reconciling;
        self.state.stale = true;
        self.state.generation += 1;
        self.state.event_sequence += 1;
        ApplyOutcome::Applied
    }

    pub fn evaluate_staleness(&mut self, now_unix_nanos: u64, max_age_nanos: u64) {
        self.state.stale =
            now_unix_nanos.saturating_sub(self.state.observed_at_unix_nanos) > max_age_nanos;
    }

    pub fn apply_snapshot(
        &mut self,
        snapshot: AccountSnapshot,
    ) -> Result<ApplyOutcome, AccountDomainError> {
        if snapshot.segment_key != self.segment.segment_key {
            return Err(AccountDomainError::SegmentMismatch {
                expected: self.segment.segment_key.to_string(),
                observed: snapshot.segment_key.to_string(),
            });
        }
        if self.state.snapshot_watermark_unix_nanos > snapshot.observed_at_unix_nanos {
            return Ok(ApplyOutcome::Stale);
        }
        if self.state.snapshot_watermark_unix_nanos != 0
            && self.state.snapshot_watermark_unix_nanos == snapshot.observed_at_unix_nanos
        {
            return Ok(ApplyOutcome::Duplicate);
        }
        let kind = snapshot.kind;
        if kind == SnapshotKind::Delta {
            for balance in snapshot.balances {
                self.state
                    .balances
                    .insert(balance.asset_id.clone(), balance);
            }
            for balance in snapshot.collateral {
                self.state
                    .collateral
                    .insert(balance.asset_id.clone(), balance);
            }
            for position in snapshot.positions {
                if position.quantity.is_zero() {
                    self.state.positions.remove(&position.instrument_id);
                } else {
                    self.state
                        .positions
                        .insert(position.instrument_id.clone(), position);
                }
            }
            for order in snapshot.open_orders {
                self.state.open_orders.insert(order.order_id.clone(), order);
            }
        } else {
            self.state.balances = snapshot
                .balances
                .into_iter()
                .map(|v| (v.asset_id.clone(), v))
                .collect();
            self.state.collateral = snapshot
                .collateral
                .into_iter()
                .map(|v| (v.asset_id.clone(), v))
                .collect();
            self.state.positions = snapshot
                .positions
                .into_iter()
                .map(|v| (v.instrument_id.clone(), v))
                .collect();
            self.state.open_orders = snapshot
                .open_orders
                .into_iter()
                .map(|v| (v.order_id.clone(), v))
                .collect();
        }
        self.state.status = snapshot.status;
        self.state.snapshot_watermark_unix_nanos = snapshot.observed_at_unix_nanos;
        if kind == SnapshotKind::Full {
            self.state.stale = false;
            self.state.observed_at_unix_nanos = snapshot.observed_at_unix_nanos;
        }
        if kind == SnapshotKind::Full || snapshot.equity.is_some() {
            self.state.equity = snapshot.equity;
        }
        if kind == SnapshotKind::Full || snapshot.initial_equity.is_some() {
            self.state.initial_equity = snapshot.initial_equity;
        }
        if kind == SnapshotKind::Full || snapshot.net_profit.is_some() {
            self.state.net_profit = snapshot.net_profit;
        }
        if kind == SnapshotKind::Full || snapshot.account_model.is_some() {
            self.state.observed_account_model = snapshot.account_model;
        }
        if kind == SnapshotKind::Full || snapshot.margin_mode.is_some() {
            self.state.margin_mode = snapshot.margin_mode;
        }
        if kind == SnapshotKind::Full || snapshot.position_mode.is_some() {
            self.state.position_mode = snapshot.position_mode;
        }
        self.state.generation += 1;
        self.state.event_sequence += 1;
        Ok(ApplyOutcome::Applied)
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SnapshotKind {
    #[default]
    Full,
    Delta,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApplyOutcome {
    Applied,
    Duplicate,
    Stale,
    NoChange,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountSnapshot {
    pub segment_key: SegmentKey,
    pub balances: Vec<Balance>,
    #[serde(default)]
    pub collateral: Vec<Balance>,
    pub positions: Vec<Position>,
    #[serde(default)]
    pub open_orders: Vec<OpenOrder>,
    pub status: AccountStatus,
    pub observed_at_unix_nanos: u64,
    pub equity: Option<Decimal>,
    pub initial_equity: Option<Decimal>,
    pub net_profit: Option<Decimal>,
    #[serde(default)]
    pub account_model: Option<AccountModel>,
    #[serde(default)]
    pub margin_mode: Option<MarginMode>,
    #[serde(default)]
    pub position_mode: Option<PositionMode>,
    #[serde(default)]
    pub kind: SnapshotKind,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct OpenOrder {
    pub order_id: String,
    pub venue_order_id: Option<String>,
    pub instrument_id: String,
    pub side: String,
    pub quantity: Decimal,
    pub filled_quantity: Decimal,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountFill {
    pub fill_id: FillId,
    #[serde(default)]
    pub order_id: Option<String>,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub quantity: Decimal,
    pub price: Decimal,
    pub side: FillSide,
    #[serde(default)]
    pub settlement_asset: Option<String>,
    #[serde(default)]
    pub settlement_delta: Option<Decimal>,
    #[serde(default)]
    pub fee_asset: Option<String>,
    #[serde(default)]
    pub fee_amount: Option<Decimal>,
    pub occurred_at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum FillSide {
    Buy,
    Sell,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AccountEvent {
    Snapshot(AccountSnapshot),
    Fill(AccountFill),
    OrderObserved(AccountOrderObservation),
    Batch(Vec<AccountEvent>),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountOrderObservation {
    pub order_id: String,
    pub venue_order_id: Option<String>,
    pub status: String,
    pub filled_quantity: Option<Decimal>,
    pub active: bool,
    pub observed_at_unix_nanos: u64,
}

impl Account {
    pub fn record_fill(&mut self, fill: AccountFill) -> Result<ApplyOutcome, AccountDomainError> {
        let state_before = self.state.clone();
        match self.record_fill_unchecked(fill) {
            Ok(outcome) => Ok(outcome),
            Err(error) => {
                self.state = state_before;
                Err(error)
            }
        }
    }

    fn record_fill_unchecked(
        &mut self,
        fill: AccountFill,
    ) -> Result<ApplyOutcome, AccountDomainError> {
        if fill.segment_key != self.segment.segment_key {
            return Err(AccountDomainError::SegmentMismatch {
                expected: self.segment.segment_key.to_string(),
                observed: fill.segment_key.to_string(),
            });
        }
        if self.state.applied_fill_ids.contains(&fill.fill_id) {
            return Ok(ApplyOutcome::Duplicate);
        }
        if fill.occurred_at_unix_nanos < self.state.fill_watermark_unix_nanos {
            return Ok(ApplyOutcome::Stale);
        }
        if !fill.quantity.is_positive() {
            return Err(AccountDomainError::Invalid {
                field: "fill.quantity",
                reason: "must be positive",
            });
        }
        if let Some(amount) = fill.fee_amount {
            if amount.is_negative() {
                return Err(AccountDomainError::Invalid {
                    field: "fill.fee_amount",
                    reason: "must not be negative",
                });
            }
        }
        let occurred_at_unix_nanos = fill.occurred_at_unix_nanos;
        let fill_id = fill.fill_id.clone();
        self.state.applied_fill_ids.insert(fill_id.clone());
        self.state.fills.insert(fill_id, fill);
        self.state.fill_watermark_unix_nanos = self
            .state
            .fill_watermark_unix_nanos
            .max(occurred_at_unix_nanos);
        self.state.event_sequence += 1;
        self.state.generation += 1;
        Ok(ApplyOutcome::Applied)
    }

    pub fn apply_order_observation(
        &mut self,
        observation: AccountOrderObservation,
    ) -> ApplyOutcome {
        let watermark = self
            .state
            .order_watermarks_unix_nanos
            .get(&observation.order_id)
            .copied()
            .unwrap_or_default();
        if observation.observed_at_unix_nanos < watermark {
            return ApplyOutcome::Stale;
        }
        if watermark != 0 && observation.observed_at_unix_nanos == watermark {
            return ApplyOutcome::Duplicate;
        }
        let Some(order) = self.state.open_orders.get_mut(&observation.order_id) else {
            return ApplyOutcome::NoChange;
        };
        self.state.order_watermarks_unix_nanos.insert(
            observation.order_id.clone(),
            observation.observed_at_unix_nanos,
        );
        if observation.active {
            order.status = observation.status;
            if observation.venue_order_id.is_some() {
                order.venue_order_id = observation.venue_order_id;
            }
            if let Some(quantity) = observation.filled_quantity {
                order.filled_quantity = quantity;
            }
        } else {
            self.state.open_orders.remove(&observation.order_id);
        }
        self.state.event_sequence += 1;
        self.state.generation += 1;
        ApplyOutcome::Applied
    }
}

pub type Accounts = BTreeMap<String, Account>;
