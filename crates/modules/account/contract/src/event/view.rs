use kairos_primitives::account::{AccountId, PositionSide, SegmentKey};
use kairos_primitives::decimal::DecimalParts;
use kairos_primitives::execution::{OrderId, OrderSide};
use kairos_primitives::integration::RemoteOrderId;
use kairos_primitives::reference::{AssetId, InstrumentId, MarketId};
use kairos_primitives::time::UnixNanos;
use kairos_protocol::generated::kairos::account::v_2 as fb;
use kairos_protocol::{BorrowedEventView, BusinessEventKind, EventMetadataOwned};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccountEventKind {
    BalanceUpserted,
    BalanceRemoved,
    PositionUpserted,
    PositionRemoved,
    EarnHoldingUpserted,
    EarnHoldingRemoved,
    ValuationChanged,
    AccountStatusChanged,
    ObservedOrderUpserted,
    ObservedOrderRemoved,
}

impl AccountEventKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::BalanceUpserted => "balance_upserted",
            Self::BalanceRemoved => "balance_removed",
            Self::PositionUpserted => "position_upserted",
            Self::PositionRemoved => "position_removed",
            Self::EarnHoldingUpserted => "earn_holding_upserted",
            Self::EarnHoldingRemoved => "earn_holding_removed",
            Self::ValuationChanged => "valuation_changed",
            Self::AccountStatusChanged => "account_status_changed",
            Self::ObservedOrderUpserted => "observed_order_upserted",
            Self::ObservedOrderRemoved => "observed_order_removed",
        }
    }
}

impl BusinessEventKind for AccountEventKind {
    fn as_str(self) -> &'static str {
        self.as_str()
    }
}

/// Borrowed Account wire event used by synchronous live visitors.
pub enum AccountEventView<'a> {
    BalanceUpserted(fb::BalanceUpserted<'a>),
    BalanceRemoved(fb::BalanceRemoved<'a>),
    PositionUpserted(fb::PositionUpserted<'a>),
    PositionRemoved(fb::PositionRemoved<'a>),
    EarnHoldingUpserted(fb::EarnHoldingUpserted<'a>),
    EarnHoldingRemoved(fb::EarnHoldingRemoved<'a>),
    ValuationChanged(fb::ValuationChanged<'a>),
    AccountStatusChanged(fb::AccountStatusChanged<'a>),
    ObservedOrderUpserted(fb::ObservedOrderUpserted<'a>),
    ObservedOrderRemoved(fb::ObservedOrderRemoved<'a>),
}

impl<'a> AccountEventView<'a> {
    pub fn kind(&self) -> AccountEventKind {
        <Self as BorrowedEventView<'a>>::kind(self)
    }

    pub fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        <Self as BorrowedEventView<'a>>::metadata(self)
    }
}

impl<'a> BorrowedEventView<'a> for AccountEventView<'a> {
    type Kind = AccountEventKind;

    fn kind(&self) -> Self::Kind {
        match self {
            Self::BalanceUpserted(_) => AccountEventKind::BalanceUpserted,
            Self::BalanceRemoved(_) => AccountEventKind::BalanceRemoved,
            Self::PositionUpserted(_) => AccountEventKind::PositionUpserted,
            Self::PositionRemoved(_) => AccountEventKind::PositionRemoved,
            Self::EarnHoldingUpserted(_) => AccountEventKind::EarnHoldingUpserted,
            Self::EarnHoldingRemoved(_) => AccountEventKind::EarnHoldingRemoved,
            Self::ValuationChanged(_) => AccountEventKind::ValuationChanged,
            Self::AccountStatusChanged(_) => AccountEventKind::AccountStatusChanged,
            Self::ObservedOrderUpserted(_) => AccountEventKind::ObservedOrderUpserted,
            Self::ObservedOrderRemoved(_) => AccountEventKind::ObservedOrderRemoved,
        }
    }

    fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        match self {
            Self::BalanceUpserted(value) => value.metadata(),
            Self::BalanceRemoved(value) => value.metadata(),
            Self::PositionUpserted(value) => value.metadata(),
            Self::PositionRemoved(value) => value.metadata(),
            Self::EarnHoldingUpserted(value) => value.metadata(),
            Self::EarnHoldingRemoved(value) => value.metadata(),
            Self::ValuationChanged(value) => value.metadata(),
            Self::AccountStatusChanged(value) => value.metadata(),
            Self::ObservedOrderUpserted(value) => value.metadata(),
            Self::ObservedOrderRemoved(value) => value.metadata(),
        }
    }
}

/// One fully owned Account event. A wire frame carries exactly one change.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountEvent {
    pub metadata: EventMetadataOwned,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub provenance: Option<AccountFactProvenance>,
    pub change: AccountChange,
}

impl AccountEvent {
    pub const fn kind(&self) -> AccountEventKind {
        self.change.kind()
    }
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
    pub const fn kind(&self) -> AccountEventKind {
        match self {
            Self::BalanceUpserted(_) => AccountEventKind::BalanceUpserted,
            Self::BalanceRemoved { .. } => AccountEventKind::BalanceRemoved,
            Self::PositionUpserted(_) => AccountEventKind::PositionUpserted,
            Self::PositionRemoved(_) => AccountEventKind::PositionRemoved,
            Self::EarnHoldingUpserted(_) => AccountEventKind::EarnHoldingUpserted,
            Self::EarnHoldingRemoved { .. } => AccountEventKind::EarnHoldingRemoved,
            Self::ValuationChanged(_) => AccountEventKind::ValuationChanged,
            Self::StatusChanged(_) => AccountEventKind::AccountStatusChanged,
            Self::ObservedOrderUpserted(_) => AccountEventKind::ObservedOrderUpserted,
            Self::ObservedOrderRemoved(_) => AccountEventKind::ObservedOrderRemoved,
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
