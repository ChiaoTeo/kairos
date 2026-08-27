use kairos_primitives::account::{AccountId, PositionSide, SegmentKey};
use kairos_primitives::decimal::DecimalParts;
use kairos_primitives::execution::{OrderId, OrderSide};
use kairos_primitives::integration::RemoteOrderId;
use kairos_primitives::reference::{AssetId, InstrumentId, MarketId};
use kairos_primitives::time::UnixNanos;
use kairos_protocol::EventMetadataOwned;

/// One fully owned Account event. A wire frame carries exactly one change.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountEvent {
    pub metadata: EventMetadataOwned,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub provenance: Option<AccountFactProvenance>,
    pub change: AccountChange,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountFactProvenance {
    pub source_id: String,
    pub provider_event_id: Option<String>,
    pub provider_sequence: Option<u64>,
    pub provider_occurred_at_unix_nanos: Option<UnixNanos>,
    pub provider_received_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AccountChange {
    BalanceUpserted(AccountBalance),
    BalanceRemoved { asset_id: AssetId },
    PositionUpserted(AccountPosition),
    PositionRemoved(AccountPositionIdentity),
    EarnHoldingUpserted(AccountEarnHolding),
    EarnHoldingRemoved { holding_key: String },
    ValuationChanged(AccountValuation),
    StatusChanged(AccountStatusChange),
    ObservedOrderUpserted(AccountObservedOrder),
    ObservedOrderRemoved(AccountObservedOrderIdentity),
}

impl AccountChange {
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::BalanceUpserted(_) => "balance_changed",
            Self::BalanceRemoved { .. } => "balance_removed",
            Self::PositionUpserted(_) => "position_changed",
            Self::PositionRemoved(_) => "position_removed",
            Self::EarnHoldingUpserted(_) => "earn_holding_changed",
            Self::EarnHoldingRemoved { .. } => "earn_holding_removed",
            Self::ValuationChanged(_) => "equity_changed",
            Self::StatusChanged(_) => "status_changed",
            Self::ObservedOrderUpserted(_) => "observed_order_changed",
            Self::ObservedOrderRemoved(_) => "observed_order_removed",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountBalance {
    pub asset_id: AssetId,
    pub asset_code: Option<String>,
    pub total: DecimalParts,
    pub available: Option<DecimalParts>,
    pub locked: Option<DecimalParts>,
    pub borrowed: Option<DecimalParts>,
    pub interest: Option<DecimalParts>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountPosition {
    pub instrument_id: InstrumentId,
    pub market_id: MarketId,
    pub position_side: PositionSide,
    pub quantity: DecimalParts,
    pub average_price: Option<DecimalParts>,
    pub mark_price: Option<DecimalParts>,
    pub unrealized_pnl: Option<DecimalParts>,
    pub realized_pnl: Option<DecimalParts>,
    pub observed_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountPositionIdentity {
    pub instrument_id: InstrumentId,
    pub market_id: MarketId,
    pub position_side: PositionSide,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EarnHoldingState {
    Active,
    Redeeming,
    Redeemed,
    Unknown,
}

impl EarnHoldingState {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Redeeming => "redeeming",
            Self::Redeemed => "redeemed",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EarnLiquidity {
    Immediate,
    Notice,
    FixedTerm,
    Unknown,
}

impl EarnLiquidity {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Immediate => "immediate",
            Self::Notice => "notice",
            Self::FixedTerm => "fixed_term",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountEarnHolding {
    pub holding_key: String,
    pub participant_position_id: Option<String>,
    pub product_id: String,
    pub asset: String,
    pub principal: DecimalParts,
    pub redeemable: Option<DecimalParts>,
    pub state: EarnHoldingState,
    pub participant_state: Option<String>,
    pub liquidity: EarnLiquidity,
    pub notice_seconds: Option<u64>,
    pub matures_at_unix_nanos: Option<UnixNanos>,
    pub observed_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountValuation {
    pub valuation_asset_id: Option<AssetId>,
    pub equity: Option<DecimalParts>,
    pub initial_equity: Option<DecimalParts>,
    pub net_profit: Option<DecimalParts>,
    pub observed_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccountStatus {
    Active,
    Restricted,
    Disabled,
    Closed,
    Error,
    Reconciling,
    TypeMismatch,
    Unavailable,
}

impl AccountStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Restricted => "restricted",
            Self::Disabled => "disabled",
            Self::Closed => "closed",
            Self::Error => "error",
            Self::Reconciling => "reconciling",
            Self::TypeMismatch => "type_mismatch",
            Self::Unavailable => "unavailable",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccountFreshness {
    Fresh,
    Stale,
    Unknown,
    Resyncing,
    Unavailable,
}

impl AccountFreshness {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Fresh => "fresh",
            Self::Stale => "stale",
            Self::Unknown => "unknown",
            Self::Resyncing => "resyncing",
            Self::Unavailable => "unavailable",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountStatusChange {
    pub status: AccountStatus,
    pub freshness: AccountFreshness,
    pub reason: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ObservedOrderStatus {
    Open,
    PartiallyFilled,
    PendingCancel,
    Closed,
    Unknown,
}

impl ObservedOrderStatus {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Open => "open",
            Self::PartiallyFilled => "partially_filled",
            Self::PendingCancel => "pending_cancel",
            Self::Closed => "closed",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountObservedOrder {
    pub observation_id: String,
    pub source_id: String,
    pub execution_order_id: Option<OrderId>,
    pub remote_order_id: Option<RemoteOrderId>,
    pub instrument_id: InstrumentId,
    pub market_id: MarketId,
    pub side: OrderSide,
    pub quantity: DecimalParts,
    pub filled_quantity: DecimalParts,
    pub status: ObservedOrderStatus,
    pub observed_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountObservedOrderIdentity {
    pub observation_id: String,
    pub execution_order_id: Option<OrderId>,
    pub remote_order_id: Option<RemoteOrderId>,
}
