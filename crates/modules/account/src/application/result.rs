use crate::domain::{
    Account, AccountId, AccountModel, AccountStatus, AssetId, Balance, InstrumentId, MarginMode,
    Money, OpenOrder, Position, PositionMode, SegmentKey,
};
use kairos_primitives::{
    ActorId, Currency, Generation, MarketId, OrderId, RemoteOrderId, Sequence, UnixNanos,
};

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AccountProjection {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub environment: String,
    pub broker: String,
    pub configured_account_model: Option<String>,
    pub observed_account_model: Option<AccountModel>,
    pub status: AccountStatus,
    pub stale: bool,
    pub observed_at_unix_nanos: UnixNanos,
    pub generation: Generation,
    pub equity: Option<Money>,
    pub initial_equity: Option<Money>,
    pub net_profit: Option<Money>,
    pub margin_mode: Option<MarginMode>,
    pub position_mode: Option<PositionMode>,
    pub balances: Vec<Balance>,
    pub collateral: Vec<Balance>,
    pub positions: Vec<Position>,
    pub open_orders: Vec<OpenOrder>,
}

impl AccountProjection {
    pub(crate) fn from_account(account: &Account) -> Self {
        let segment = account.segment();
        let state = account.state();
        Self {
            account_id: segment.identity.account_id.clone(),
            segment_key: segment.segment_key.clone(),
            environment: segment.environment.clone(),
            broker: segment.identity.broker.clone(),
            configured_account_model: segment.account_model.clone(),
            observed_account_model: state.observed_account_model(),
            status: state.status(),
            stale: state.stale(),
            observed_at_unix_nanos: state.observed_at_unix_nanos(),
            generation: state.generation(),
            equity: state.equity(),
            initial_equity: state.initial_equity(),
            net_profit: state.net_profit(),
            margin_mode: state.margin_mode(),
            position_mode: state.position_mode(),
            balances: state.balances().values().cloned().collect(),
            collateral: state.collateral().values().cloned().collect(),
            positions: state.positions().values().cloned().collect(),
            open_orders: state.open_orders().values().cloned().collect(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountRefreshIssue {
    pub segment_key: SegmentKey,
    pub error: String,
    pub elapsed_ms: u64,
    pub diagnostic_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountDifference {
    pub field: String,
    pub key: String,
    pub local: crate::domain::SignedQuantity,
    pub external: crate::domain::SignedQuantity,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountRefreshReport {
    pub account_id: AccountId,
    pub refreshed_segments: Vec<SegmentKey>,
    pub issues: Vec<AccountRefreshIssue>,
    #[serde(default)]
    pub differences: Vec<AccountDifference>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountsSnapshot {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub accounts: Vec<AccountProjection>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountFactProvenance {
    pub source_id: String,
    pub provider_event_id: Option<String>,
    pub provider_sequence: Option<u64>,
    pub provider_occurred_at_unix_nanos: Option<u64>,
    pub provider_received_at_unix_nanos: Option<u64>,
}

/// A Strategy-visible Account fact emitted by the Account Actor at the same
/// transition that changed its owned state. This is not a snapshot and is
/// never reconstructed by a snapshot publisher.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountBusinessEvent {
    pub sequence: Sequence,
    pub account_id: AccountId,
    pub occurred_at_unix_nanos: UnixNanos,
    pub changes: Vec<AccountBusinessChange>,
    pub provenance: Option<AccountFactProvenance>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AccountBusinessChange {
    Balance {
        segment_key: SegmentKey,
        value: Balance,
    },
    BalanceRemoved {
        segment_key: SegmentKey,
        asset_id: AssetId,
    },
    Position {
        segment_key: SegmentKey,
        value: Position,
    },
    PositionRemoved {
        segment_key: SegmentKey,
        instrument_id: InstrumentId,
        market_id: Option<MarketId>,
    },
    ObservedOrder {
        segment_key: SegmentKey,
        value: OpenOrder,
    },
    ObservedOrderRemoved {
        segment_key: SegmentKey,
        order_id: OrderId,
        remote_order_id: Option<RemoteOrderId>,
    },
    Equity {
        segment_key: SegmentKey,
        value: Option<Money>,
    },
    Status {
        segment_key: SegmentKey,
        status: AccountStatus,
        stale: bool,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountCapability {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub can_trade: bool,
    pub can_hold_assets: bool,
    pub can_hold_position: bool,
    pub can_borrow: bool,
    pub can_transfer_in: bool,
    pub can_transfer_out: bool,
    pub supported_order_types: Vec<String>,
    pub settlement_assets: Vec<Currency>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountFeeSchedule {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub maker: Option<kairos_primitives::Rate>,
    pub taker: Option<kairos_primitives::Rate>,
    pub currency: Option<Currency>,
    pub tier: Option<String>,
    pub source: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountBalanceRow {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub balance: Balance,
}
