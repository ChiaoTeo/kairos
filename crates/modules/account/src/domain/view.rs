//! Account-owned current business facts derived from Actor state.

use kairos_primitives::account::BrokerId;
use kairos_primitives::execution::OrderId;
use kairos_primitives::integration::RemoteOrderId;
use kairos_primitives::reference::MarketId;
use kairos_primitives::runtime::ActorId;
use kairos_primitives::time::{Generation, Sequence, UnixNanos};

use super::{
    Account, AccountId, AccountModel, AccountStatus, AssetId, Balance, EarnHolding, InstrumentId,
    MarginMode, Money, OpenOrder, Position, PositionMode, SegmentKey, SignedQuantity,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountSegmentSyncMode {
    Unknown,
    SnapshotThenStream,
    SnapshotOnly,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountSegmentSyncLifecycle {
    Configured,
    Bootstrapping,
    Live,
    SnapshotCurrent,
    Degraded,
    Resyncing,
    Unavailable,
    Stopped,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountSegmentFreshness {
    Fresh,
    Stale,
    Resyncing,
    Unavailable,
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountSegmentCompleteness {
    Complete,
    Partial,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AccountSegmentView {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub environment: String,
    pub broker: BrokerId,
    pub configured_account_model: Option<String>,
    pub observed_account_model: Option<AccountModel>,
    pub status: AccountStatus,
    pub freshness: AccountSegmentFreshness,
    pub sync_mode: AccountSegmentSyncMode,
    pub sync_lifecycle: AccountSegmentSyncLifecycle,
    pub completeness: AccountSegmentCompleteness,
    pub snapshot_watermark: Option<Sequence>,
    pub event_watermark: Option<Sequence>,
    pub channel_epoch: Option<Generation>,
    pub last_event_at_unix_nanos: Option<UnixNanos>,
    pub last_success_at_unix_nanos: Option<UnixNanos>,
    pub last_error: Option<String>,
    pub recovery_buffer_depth: u64,
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
    pub earn_holdings: Vec<EarnHolding>,
    pub earn_watermark_unix_nanos: UnixNanos,
    pub open_orders: Vec<OpenOrder>,
}

impl AccountSegmentView {
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
            freshness: if state.stale() {
                AccountSegmentFreshness::Stale
            } else {
                AccountSegmentFreshness::Fresh
            },
            sync_mode: AccountSegmentSyncMode::Unknown,
            sync_lifecycle: AccountSegmentSyncLifecycle::Configured,
            completeness: AccountSegmentCompleteness::Unknown,
            snapshot_watermark: None,
            event_watermark: None,
            channel_epoch: None,
            last_event_at_unix_nanos: None,
            last_success_at_unix_nanos: None,
            last_error: None,
            recovery_buffer_depth: 0,
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
            earn_holdings: state.earn_holdings().values().cloned().collect(),
            earn_watermark_unix_nanos: state.earn_watermark_unix_nanos(),
            open_orders: state.open_orders().values().cloned().collect(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountDifference {
    pub field: String,
    pub key: String,
    pub local: SignedQuantity,
    pub external: SignedQuantity,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountCurrentView {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub segments: Vec<AccountSegmentView>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct AccountFactProvenance {
    pub source_id: kairos_primitives::integration::IntegrationSourceId,
    pub provider_event_id: Option<String>,
    pub provider_sequence: Option<Sequence>,
    pub provider_occurred_at_unix_nanos: Option<UnixNanos>,
    pub provider_received_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct AccountBusinessEvent {
    pub sequence: Sequence,
    pub account_id: AccountId,
    pub occurred_at_unix_nanos: UnixNanos,
    pub changes: Vec<AccountBusinessChange>,
    pub provenance: Option<AccountFactProvenance>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
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
        position_side: kairos_primitives::account::PositionSide,
    },
    EarnHolding {
        segment_key: SegmentKey,
        value: EarnHolding,
    },
    EarnHoldingRemoved {
        segment_key: SegmentKey,
        holding_key: String,
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
