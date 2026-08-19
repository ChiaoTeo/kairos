use crate::domain::{
    Account, AccountId, AccountModel, AccountStatus, AssetId, Balance, EarnHolding, InstrumentId,
    MarginMode, Money, OpenOrder, Position, PositionMode, SegmentKey,
};
use kairos_primitives::{
    ActorId, BrokerId, Generation, MarketId, OrderId, RemoteOrderId, Sequence, UnixNanos,
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
    pub snapshot_watermark: Option<u64>,
    pub event_watermark: Option<u64>,
    pub channel_epoch: Option<u64>,
    pub last_event_at_unix_nanos: Option<u64>,
    pub last_success_at_unix_nanos: Option<u64>,
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
pub struct AccountCurrentView {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub segments: Vec<AccountSegmentView>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
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
        position_side: kairos_primitives::PositionSide,
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
