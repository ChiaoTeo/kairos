use std::collections::BTreeMap;

use kairos_primitives::account::BrokerId;
use kairos_primitives::capital::EarnProductId;
use kairos_primitives::execution::OrderId;
use kairos_primitives::integration::{OrderStatus, RemoteOrderId};
use kairos_primitives::reference::{Currency, MarketId};
use kairos_primitives::runtime::IdempotencyKey;
use kairos_primitives::time::{DurationNanos, Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

mod error;
mod view;
pub use error::AccountDomainError;
pub use kairos_primitives::account::{AccountId, SegmentKey};
pub use kairos_primitives::decimal::{Money, Price, Quantity, SignedQuantity};
pub use kairos_primitives::execution::{FillId, OrderSide};
pub use kairos_primitives::reference::{AssetId, InstrumentId};
pub use view::{
    AccountBusinessChange, AccountBusinessEvent, AccountCurrentView, AccountDifference,
    AccountFactProvenance, AccountSegmentCompleteness, AccountSegmentFreshness,
    AccountSegmentSyncLifecycle, AccountSegmentSyncMode, AccountSegmentView,
};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ExternalAccountIdentity {
    pub broker: BrokerId,
    pub account_id: AccountId,
}

impl ExternalAccountIdentity {
    pub fn new(
        broker: impl Into<String>,
        account_id: impl Into<String>,
    ) -> Result<Self, AccountDomainError> {
        let broker = BrokerId::new(broker.into()).map_err(|error| match error {
            kairos_primitives::DomainTypeError::Empty { .. } => {
                AccountDomainError::Required { field: "broker" }
            },
            _ => AccountDomainError::Invalid {
                field: "broker",
                reason: "broker identity must be non-empty without surrounding whitespace",
            },
        })?;
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
    pub asset_code: Currency,
    pub total: SignedQuantity,
    pub available: Option<SignedQuantity>,
    pub locked: Option<SignedQuantity>,
    pub borrowed: Option<SignedQuantity>,
    pub interest: Option<SignedQuantity>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Position {
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    #[serde(default)]
    pub position_side: kairos_primitives::account::PositionSide,
    pub quantity: SignedQuantity,
    pub average_price: Option<Price>,
    pub mark_price: Option<Price>,
    pub unrealized_pnl: Option<Money>,
    pub realized_pnl: Option<Money>,
    pub updated_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EarnHoldingLiquidity {
    Immediate,
    Notice { notice_seconds: u64 },
    FixedTerm { matures_at_unix_nanos: UnixNanos },
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EarnHoldingState {
    Active,
    Redeeming,
    Redeemed,
    Unknown(String),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EarnAccruedReward {
    pub asset: Currency,
    pub amount: Quantity,
}

/// Account-owned observation of principal placed in a yield product. This is
/// deliberately not a trading Position.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EarnHolding {
    pub participant_position_id: Option<String>,
    pub product_id: EarnProductId,
    pub asset: Currency,
    pub principal: Quantity,
    pub redeemable: Option<Quantity>,
    pub accrued_rewards: Vec<EarnAccruedReward>,
    pub liquidity: EarnHoldingLiquidity,
    pub state: EarnHoldingState,
    pub observed_at_unix_nanos: UnixNanos,
}

impl EarnHolding {
    fn key(&self) -> String {
        self.participant_position_id
            .clone()
            .unwrap_or_else(|| self.product_id.to_string())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EarnHoldingsSnapshot {
    pub segment_key: SegmentKey,
    pub holdings: Vec<EarnHolding>,
    pub observed_at_unix_nanos: UnixNanos,
    pub complete: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SimulatedCapitalMutationKind {
    DebitLiquid,
    CreditLiquid,
    SubscribeEarn,
    RedeemEarn,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulatedCapitalMutation {
    pub mutation_id: IdempotencyKey,
    pub segment_key: SegmentKey,
    pub asset: Currency,
    pub amount: Quantity,
    pub kind: SimulatedCapitalMutationKind,
    pub product_id: Option<EarnProductId>,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum AccountStatus {
    #[default]
    Unknown,
    Ready,
    Reconciling,
    TypeMismatch,
    Suspended,
    Unavailable,
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
    positions: BTreeMap<(InstrumentId, kairos_primitives::account::PositionSide), Position>,
    #[serde(default)]
    earn_holdings: BTreeMap<String, EarnHolding>,
    open_orders: BTreeMap<OrderId, OpenOrder>,
    status: AccountStatus,
    stale: bool,
    observed_at_unix_nanos: UnixNanos,
    generation: Generation,
    event_sequence: Sequence,
    equity: Option<Money>,
    initial_equity: Option<Money>,
    net_profit: Option<Money>,
    observed_account_model: Option<AccountModel>,
    margin_mode: Option<MarginMode>,
    position_mode: Option<PositionMode>,
    #[serde(default)]
    snapshot_watermark_unix_nanos: UnixNanos,
    #[serde(default)]
    fill_watermark_unix_nanos: UnixNanos,
    #[serde(default)]
    earn_watermark_unix_nanos: UnixNanos,
    #[serde(default)]
    order_watermarks_unix_nanos: BTreeMap<OrderId, UnixNanos>,
    #[serde(default)]
    applied_fill_ids: std::collections::BTreeSet<FillId>,
    #[serde(default)]
    fills: BTreeMap<FillId, AccountFill>,
    #[serde(default)]
    observed_fills: BTreeMap<FillId, AccountObservedFill>,
    #[serde(default)]
    applied_capital_mutation_ids: std::collections::BTreeSet<IdempotencyKey>,
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

    pub fn positions(
        &self,
    ) -> &BTreeMap<(InstrumentId, kairos_primitives::account::PositionSide), Position> {
        &self.positions
    }

    pub fn earn_holdings(&self) -> &BTreeMap<String, EarnHolding> {
        &self.earn_holdings
    }

    pub fn earn_watermark_unix_nanos(&self) -> UnixNanos {
        self.earn_watermark_unix_nanos
    }

    pub fn open_orders(&self) -> &BTreeMap<OrderId, OpenOrder> {
        &self.open_orders
    }

    pub fn has_capital_mutation(&self, mutation_id: &IdempotencyKey) -> bool {
        self.applied_capital_mutation_ids.contains(mutation_id)
    }

    pub fn status(&self) -> AccountStatus {
        self.status
    }

    pub fn stale(&self) -> bool {
        self.stale
    }

    pub fn observed_at_unix_nanos(&self) -> UnixNanos {
        self.observed_at_unix_nanos
    }

    pub fn generation(&self) -> Generation {
        self.generation
    }

    pub fn event_sequence(&self) -> Sequence {
        self.event_sequence
    }

    pub fn equity(&self) -> Option<Money> {
        self.equity
    }

    pub fn initial_equity(&self) -> Option<Money> {
        self.initial_equity
    }

    pub fn net_profit(&self) -> Option<Money> {
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

    pub fn status(&self) -> AccountStatus {
        self.state.status()
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

    pub fn evaluate_staleness(&mut self, now_unix_nanos: UnixNanos, max_age_nanos: DurationNanos) {
        self.state.stale = now_unix_nanos.saturating_sub(self.state.observed_at_unix_nanos)
            > UnixNanos::new(max_age_nanos.get());
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
        if self.state.snapshot_watermark_unix_nanos != UnixNanos::new(0)
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
                let key = (position.instrument_id.clone(), position.position_side);
                if position.quantity.is_zero() {
                    self.state.positions.remove(&key);
                } else {
                    self.state.positions.insert(key, position);
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
                .map(|v| ((v.instrument_id.clone(), v.position_side), v))
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

    pub fn apply_earn_snapshot(
        &mut self,
        snapshot: EarnHoldingsSnapshot,
    ) -> Result<ApplyOutcome, AccountDomainError> {
        if snapshot.segment_key != self.segment.segment_key {
            return Err(AccountDomainError::SegmentMismatch {
                expected: self.segment.segment_key.to_string(),
                observed: snapshot.segment_key.to_string(),
            });
        }
        if self.state.earn_watermark_unix_nanos > snapshot.observed_at_unix_nanos {
            return Ok(ApplyOutcome::Stale);
        }
        if self.state.earn_watermark_unix_nanos == snapshot.observed_at_unix_nanos
            && snapshot.observed_at_unix_nanos != UnixNanos::new(0)
        {
            return Ok(ApplyOutcome::Duplicate);
        }
        if snapshot.complete {
            self.state.earn_holdings = snapshot
                .holdings
                .into_iter()
                .map(|holding| (holding.key(), holding))
                .collect();
        } else {
            for holding in snapshot.holdings {
                self.state.earn_holdings.insert(holding.key(), holding);
            }
        }
        self.state.earn_watermark_unix_nanos = snapshot.observed_at_unix_nanos;
        self.state.observed_at_unix_nanos = self
            .state
            .observed_at_unix_nanos
            .max(snapshot.observed_at_unix_nanos);
        self.state.generation += 1;
        self.state.event_sequence += 1;
        Ok(ApplyOutcome::Applied)
    }

    pub fn apply_simulated_capital_mutation(
        &mut self,
        mutation: SimulatedCapitalMutation,
    ) -> Result<ApplyOutcome, AccountDomainError> {
        if mutation.segment_key != self.segment.segment_key {
            return Err(AccountDomainError::SegmentMismatch {
                expected: self.segment.segment_key.to_string(),
                observed: mutation.segment_key.to_string(),
            });
        }
        if mutation.amount.is_zero() {
            return Err(AccountDomainError::Invalid {
                field: "amount",
                reason: "simulated Capital mutation amount must be positive",
            });
        }
        if self
            .state
            .applied_capital_mutation_ids
            .contains(&mutation.mutation_id)
        {
            return Ok(ApplyOutcome::Duplicate);
        }
        if mutation.occurred_at_unix_nanos < self.state.observed_at_unix_nanos {
            return Ok(ApplyOutcome::Stale);
        }
        match mutation.kind {
            SimulatedCapitalMutationKind::SubscribeEarn => {
                let product_id = required_simulated_product_id(&mutation)?;
                if let Some(holding) = self.state.earn_holdings.get(product_id.as_str()) {
                    if holding.asset != mutation.asset {
                        return Err(AccountDomainError::Invalid {
                            field: "asset",
                            reason: "simulated Earn product asset cannot change",
                        });
                    }
                    holding.principal.checked_add(mutation.amount)?;
                    holding
                        .redeemable
                        .unwrap_or(Quantity::ZERO)
                        .checked_add(mutation.amount)?;
                }
            },
            SimulatedCapitalMutationKind::RedeemEarn => {
                let product_id = required_simulated_product_id(&mutation)?;
                let holding = self.state.earn_holdings.get(product_id.as_str()).ok_or(
                    AccountDomainError::Invalid {
                        field: "product_id",
                        reason: "simulated Earn holding was not found",
                    },
                )?;
                if holding.asset != mutation.asset {
                    return Err(AccountDomainError::Invalid {
                        field: "asset",
                        reason: "simulated Earn redemption asset does not match the holding",
                    });
                }
                holding.principal.checked_sub(mutation.amount)?;
                holding
                    .redeemable
                    .unwrap_or(Quantity::ZERO)
                    .checked_sub(mutation.amount)?;
            },
            SimulatedCapitalMutationKind::DebitLiquid
            | SimulatedCapitalMutationKind::CreditLiquid => {},
        }
        let asset_id = self
            .state
            .balances
            .iter()
            .find_map(|(asset_id, balance)| {
                (balance.asset_code == mutation.asset).then(|| asset_id.clone())
            })
            .unwrap_or(AssetId::new(format!(
                "asset:{}",
                mutation.asset.as_str().to_ascii_lowercase()
            ))?);
        let amount = SignedQuantity::new(mutation.amount.mantissa(), mutation.amount.scale())?;
        match mutation.kind {
            SimulatedCapitalMutationKind::DebitLiquid
            | SimulatedCapitalMutationKind::SubscribeEarn => {
                adjust_simulated_liquid_balance(
                    &mut self.state.balances,
                    &asset_id,
                    &mutation.asset,
                    amount.checked_neg()?,
                    true,
                )?;
            },
            SimulatedCapitalMutationKind::CreditLiquid
            | SimulatedCapitalMutationKind::RedeemEarn => {
                adjust_simulated_liquid_balance(
                    &mut self.state.balances,
                    &asset_id,
                    &mutation.asset,
                    amount,
                    false,
                )?;
            },
        }
        match mutation.kind {
            SimulatedCapitalMutationKind::SubscribeEarn => {
                let product_id = required_simulated_product_id(&mutation)?;
                let holding = self
                    .state
                    .earn_holdings
                    .entry(product_id.to_string())
                    .or_insert_with(|| EarnHolding {
                        participant_position_id: None,
                        product_id: product_id.clone(),
                        asset: mutation.asset.clone(),
                        principal: Quantity::ZERO,
                        redeemable: Some(Quantity::ZERO),
                        accrued_rewards: Vec::new(),
                        liquidity: EarnHoldingLiquidity::Immediate,
                        state: EarnHoldingState::Active,
                        observed_at_unix_nanos: mutation.occurred_at_unix_nanos,
                    });
                if holding.asset != mutation.asset {
                    return Err(AccountDomainError::Invalid {
                        field: "asset",
                        reason: "simulated Earn product asset cannot change",
                    });
                }
                holding.principal = holding.principal.checked_add(mutation.amount)?;
                holding.redeemable = Some(
                    holding
                        .redeemable
                        .unwrap_or(Quantity::ZERO)
                        .checked_add(mutation.amount)?,
                );
                holding.state = EarnHoldingState::Active;
                holding.observed_at_unix_nanos = mutation.occurred_at_unix_nanos;
                self.state.earn_watermark_unix_nanos = mutation.occurred_at_unix_nanos;
            },
            SimulatedCapitalMutationKind::RedeemEarn => {
                let product_id = required_simulated_product_id(&mutation)?;
                let holding = self
                    .state
                    .earn_holdings
                    .get_mut(product_id.as_str())
                    .ok_or(AccountDomainError::Invalid {
                        field: "product_id",
                        reason: "simulated Earn holding was not found",
                    })?;
                holding.principal = holding.principal.checked_sub(mutation.amount)?;
                holding.redeemable = Some(
                    holding
                        .redeemable
                        .unwrap_or(Quantity::ZERO)
                        .checked_sub(mutation.amount)?,
                );
                holding.state = if holding.principal.is_zero() {
                    EarnHoldingState::Redeemed
                } else {
                    EarnHoldingState::Active
                };
                holding.observed_at_unix_nanos = mutation.occurred_at_unix_nanos;
                self.state.earn_watermark_unix_nanos = mutation.occurred_at_unix_nanos;
            },
            SimulatedCapitalMutationKind::DebitLiquid
            | SimulatedCapitalMutationKind::CreditLiquid => {},
        }
        self.state
            .applied_capital_mutation_ids
            .insert(mutation.mutation_id);
        self.state.snapshot_watermark_unix_nanos = mutation.occurred_at_unix_nanos;
        self.state.observed_at_unix_nanos = mutation.occurred_at_unix_nanos;
        self.state.status = AccountStatus::Ready;
        self.state.stale = false;
        self.state.generation += 1;
        self.state.event_sequence += 1;
        Ok(ApplyOutcome::Applied)
    }
}

fn required_simulated_product_id(
    mutation: &SimulatedCapitalMutation,
) -> Result<&EarnProductId, AccountDomainError> {
    mutation
        .product_id
        .as_ref()
        .ok_or(AccountDomainError::Required {
            field: "product_id",
        })
}

fn adjust_simulated_liquid_balance(
    balances: &mut BTreeMap<AssetId, Balance>,
    asset_id: &AssetId,
    asset: &Currency,
    delta: SignedQuantity,
    require_available: bool,
) -> Result<(), AccountDomainError> {
    let balance = balances.entry(asset_id.clone()).or_insert(Balance {
        asset_id: asset_id.clone(),
        asset_code: asset.clone(),
        total: SignedQuantity::ZERO,
        available: Some(SignedQuantity::ZERO),
        locked: Some(SignedQuantity::ZERO),
        borrowed: None,
        interest: None,
    });
    let available = balance.available.unwrap_or(balance.total);
    let next_available = available.checked_add(delta)?;
    let next_total = balance.total.checked_add(delta)?;
    if require_available && (next_available.is_negative() || next_total.is_negative()) {
        return Err(AccountDomainError::Invalid {
            field: "amount",
            reason: "simulated Capital mutation exceeds liquid balance",
        });
    }
    balance.available = Some(next_available);
    balance.total = next_total;
    Ok(())
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
    Conflict,
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
    pub observed_at_unix_nanos: UnixNanos,
    pub equity: Option<Money>,
    pub initial_equity: Option<Money>,
    pub net_profit: Option<Money>,
    #[serde(default)]
    pub account_model: Option<AccountModel>,
    #[serde(default)]
    pub margin_mode: Option<MarginMode>,
    #[serde(default)]
    pub position_mode: Option<PositionMode>,
    #[serde(default)]
    pub kind: SnapshotKind,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OpenOrder {
    pub order_id: OrderId,
    pub remote_order_id: Option<RemoteOrderId>,
    pub instrument_id: InstrumentId,
    #[serde(default)]
    pub market_id: Option<MarketId>,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub filled_quantity: Quantity,
    pub status: OrderStatus,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountFill {
    pub fill_id: FillId,
    #[serde(default)]
    pub order_id: Option<OrderId>,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub quantity: Quantity,
    pub price: Price,
    pub side: OrderSide,
    #[serde(default)]
    pub settlement_asset: Option<Currency>,
    #[serde(default)]
    pub settlement_delta: Option<SignedQuantity>,
    #[serde(default)]
    pub fee_asset: Option<Currency>,
    #[serde(default)]
    pub fee_amount: Option<SignedQuantity>,
    pub occurred_at_unix_nanos: UnixNanos,
}

/// A fill observed by the account-side private stream before Execution has
/// confirmed the exchange order. This is an audit/reconciliation fact, not a
/// settlement command and must not mutate balances or positions.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountObservedFill {
    pub fill_id: FillId,
    pub order_id: Option<OrderId>,
    pub remote_order_id: Option<RemoteOrderId>,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub quantity: Quantity,
    pub price: Price,
    pub side: OrderSide,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AccountEvent {
    Snapshot(AccountSnapshot),
    EarnHoldings(EarnHoldingsSnapshot),
    Fill(AccountFill),
    ObservedFill(AccountObservedFill),
    OrderObserved(AccountOrderObservation),
    SimulatedCapitalMutation(SimulatedCapitalMutation),
    Batch(Vec<AccountEvent>),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountOrderObservation {
    pub order_id: OrderId,
    pub remote_order_id: Option<RemoteOrderId>,
    pub status: OrderStatus,
    pub filled_quantity: Option<Quantity>,
    pub active: bool,
    pub observed_at_unix_nanos: UnixNanos,
}

impl Account {
    pub fn record_fill(&mut self, fill: AccountFill) -> Result<ApplyOutcome, AccountDomainError> {
        let state_before = self.state.clone();
        match self.record_fill_unchecked(fill) {
            Ok(outcome) => Ok(outcome),
            Err(error) => {
                self.state = state_before;
                Err(error)
            },
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
        if let Some(existing) = self.state.fills.get(&fill.fill_id) {
            if existing == &fill {
                return Ok(ApplyOutcome::Duplicate);
            }
            self.state.status = AccountStatus::Reconciling;
            self.state.generation += 1;
            self.state.event_sequence += 1;
            return Ok(ApplyOutcome::Conflict);
        }
        if let Some(observed) = self.state.observed_fills.get(&fill.fill_id) {
            let same_fact = observed.order_id == fill.order_id
                && observed.segment_key == fill.segment_key
                && observed.instrument_id == fill.instrument_id
                && observed.quantity == fill.quantity
                && observed.price == fill.price
                && observed.side == fill.side
                && observed.occurred_at_unix_nanos == fill.occurred_at_unix_nanos;
            if !same_fact {
                self.state.status = AccountStatus::Reconciling;
                self.state.generation += 1;
                self.state.event_sequence += 1;
                return Ok(ApplyOutcome::Conflict);
            }
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
        self.state.fills.insert(fill_id.clone(), fill);
        self.state.observed_fills.remove(&fill_id);
        self.state.fill_watermark_unix_nanos = self
            .state
            .fill_watermark_unix_nanos
            .max(occurred_at_unix_nanos);
        self.state.event_sequence += 1;
        self.state.generation += 1;
        Ok(ApplyOutcome::Applied)
    }

    pub fn observe_fill(
        &mut self,
        fill: AccountObservedFill,
    ) -> Result<ApplyOutcome, AccountDomainError> {
        if fill.segment_key != self.segment.segment_key {
            return Err(AccountDomainError::SegmentMismatch {
                expected: self.segment.segment_key.to_string(),
                observed: fill.segment_key.to_string(),
            });
        }
        if let Some(existing) = self.state.observed_fills.get(&fill.fill_id) {
            if existing == &fill {
                return Ok(ApplyOutcome::Duplicate);
            }
            self.state.status = AccountStatus::Reconciling;
            self.state.generation += 1;
            self.state.event_sequence += 1;
            return Ok(ApplyOutcome::Conflict);
        }
        if self.state.fills.contains_key(&fill.fill_id) {
            return Ok(ApplyOutcome::Duplicate);
        }
        self.state.observed_fills.insert(fill.fill_id.clone(), fill);
        self.state.status = AccountStatus::Reconciling;
        self.state.stale = true;
        self.state.generation += 1;
        self.state.event_sequence += 1;
        Ok(ApplyOutcome::Applied)
    }

    pub fn observed_fills(&self) -> &BTreeMap<FillId, AccountObservedFill> {
        &self.state.observed_fills
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
        if watermark != UnixNanos::new(0) && observation.observed_at_unix_nanos == watermark {
            return ApplyOutcome::Duplicate;
        }
        let order_id = observation.order_id.clone();
        let Some(order) = self.state.open_orders.get_mut(&order_id) else {
            return ApplyOutcome::NoChange;
        };
        self.state
            .order_watermarks_unix_nanos
            .insert(observation.order_id, observation.observed_at_unix_nanos);
        if observation.active {
            order.status = observation.status;
            if observation.remote_order_id.is_some() {
                order.remote_order_id = observation.remote_order_id;
            }
            if let Some(quantity) = observation.filled_quantity {
                order.filled_quantity = quantity;
            }
        } else {
            self.state.open_orders.remove(&order_id);
        }
        self.state.event_sequence += 1;
        self.state.generation += 1;
        ApplyOutcome::Applied
    }
}

pub type Accounts = BTreeMap<String, Account>;
