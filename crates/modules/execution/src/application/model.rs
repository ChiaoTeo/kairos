//! Execution application commands, results, views, and errors.

use kairos_primitives::{
    AccountId, ActorId, ClientOrderId, Currency, ExecutionAccessId, FillId, Generation,
    InstrumentId, IntentId, LegId, MarketId, Money, OrderId, PlanId, Price, Quantity,
    RemoteOrderId, SegmentKey, Sequence, Symbol, UnixNanos,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

use crate::domain::{
    CompletionPolicy, ExecutionFill, ExecutionOrder, ExecutionOrderStatus, ExecutionPlan,
    FailurePolicy, HedgePolicy, IntentType, MakerExecutionPolicy, OrderCommitment, OrderSide,
    OrderType, RiskReservationEvidence, SplitOrderPolicy,
};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SubmitOrder {
    pub order_id: OrderId,
    pub intent_id: Option<IntentId>,
    #[serde(default)]
    pub strategy_id: Option<kairos_primitives::StrategyId>,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    /// Explicit Reference ExecutionAccess selected by planning or the caller.
    /// Execution never derives provider identity from a MarketId or symbol.
    #[serde(default)]
    pub execution_access_id: Option<ExecutionAccessId>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Quantity,
    pub limit_price: Option<Price>,
    pub options: ExecutionOrderOptions,
    /// Business time of the causal event. `None` means use processing time.
    pub submitted_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionOrderOptions {
    pub time_in_force: Option<String>,
    pub reduce_only: Option<bool>,
    pub post_only: Option<bool>,
    pub position_side: Option<String>,
    pub quote_asset: Option<String>,
    pub wallet_type: Option<String>,
    pub trading_session: Option<String>,
    pub tokenize: Option<bool>,
    #[serde(default)]
    pub split: Option<SplitOrderPolicy>,
    #[serde(default)]
    pub maker: Option<MakerExecutionPolicy>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelOrder {
    pub order_id: OrderId,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelIntent {
    pub intent_id: IntentId,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExpireIntent {
    pub intent_id: IntentId,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReplaceOrder {
    pub order_id: OrderId,
    pub replacement: SubmitOrder,
}

/// Replace the two live legs of a QuoteProvisioning intent as one
/// Execution-owned operation.  Strategies provide a fresh quote; Execution
/// owns the cancel/re-submit sequence and keeps the old orders in the same
/// plan for audit and fill aggregation.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RefreshQuoteIntent {
    pub intent_id: IntentId,
    pub bid_price: Price,
    pub ask_price: Price,
    pub quote_observed_at: UnixNanos,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct QuoteObservation {
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    pub bid_price: Option<Price>,
    pub ask_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionFillReport {
    pub fill_id: FillId,
    pub order_id: OrderId,
    pub quantity: Quantity,
    pub price: Price,
    pub fee: Money,
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    pub occurred_at_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub execution_market_id: Option<MarketId>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionEvent {
    pub order_id: OrderId,
    #[serde(default)]
    pub intent_id: Option<IntentId>,
    #[serde(default)]
    pub plan_id: Option<PlanId>,
    #[serde(default)]
    pub leg_id: Option<LegId>,
    pub status: ExecutionOrderStatus,
    pub remote_order_id: Option<RemoteOrderId>,
    pub occurred_at_unix_nanos: UnixNanos,
    pub reason: String,
    #[serde(default)]
    pub fill_id: Option<FillId>,
    #[serde(default)]
    pub filled_quantity: Option<Quantity>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionBusinessEvent {
    pub sequence: Sequence,
    pub occurred_at_unix_nanos: UnixNanos,
    pub changes: Vec<ExecutionBusinessChange>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExecutionBusinessChange {
    Intent(IntentState),
    Order {
        strategy_id: String,
        order: ExecutionOrder,
    },
    Fill {
        strategy_id: String,
        account_id: String,
        intent_id: Option<String>,
        market_id: Option<String>,
        remote_order_id: Option<String>,
        side: OrderSide,
        fill: ExecutionFill,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionSnapshot {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub orders: Vec<ExecutionOrder>,
    #[serde(default)]
    pub events: Vec<ExecutionEvent>,
    #[serde(default)]
    pub fills: Vec<ExecutionFill>,
    #[serde(default)]
    pub commitments: Vec<OrderCommitment>,
    #[serde(default)]
    pub risk_reservations: Vec<RiskReservationEvidence>,
    #[serde(default)]
    pub intents: Vec<IntentState>,
    #[serde(default)]
    pub intent_events: Vec<IntentEvent>,
    #[serde(default)]
    pub intent_idempotency: BTreeMap<String, String>,
    #[serde(default)]
    pub unknown_remote_orders: Vec<UnknownRemoteOrder>,
    #[serde(default)]
    pub exchange_event_watermark_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ExecutionCurrentView {
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub orders: Vec<ExecutionOrder>,
    pub commitments: Vec<OrderCommitment>,
    pub risk_reservations: Vec<RiskReservationEvidence>,
    pub intents: Vec<IntentState>,
    pub events: Vec<ExecutionEvent>,
    pub intent_events: Vec<IntentEvent>,
    pub fills: Vec<ExecutionFill>,
    pub unknown_remote_orders: Vec<UnknownRemoteOrder>,
    pub exchange_event_watermark_unix_nanos: UnixNanos,
}

/// A exchange order observed through a private stream or remote query that could
/// not be associated with a locally submitted order.  It is deliberately
/// persisted instead of being discarded or treated as a transient gateway
/// error: recovery must be able to inspect and resolve it after a restart.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct UnknownRemoteOrder {
    pub remote_order_id: kairos_primitives::RemoteOrderId,
    pub symbol: Symbol,
    pub status: ExecutionOrderStatus,
    pub execution_id: Option<FillId>,
    pub fill_quantity: Option<Quantity>,
    pub fill_price: Option<Price>,
    pub fee_currency: Option<Currency>,
    pub fee_amount: Option<Money>,
    pub first_seen_at_unix_nanos: UnixNanos,
    pub last_seen_at_unix_nanos: UnixNanos,
    pub resolution: UnknownRemoteOrderResolution,
    pub reason: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum UnknownRemoteOrderResolution {
    Pending,
    LinkedToLocalOrder,
    ImportedAsExternalOrder,
    ManualReview,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct RemoteOrderQuery {
    /// Integration binding selected for a provider query. When omitted, a
    /// business-owned route set may query every configured binding.
    pub binding_id: Option<String>,
    pub symbol: Option<Symbol>,
    pub order_id: Option<OrderId>,
    pub limit: Option<u32>,
    pub since_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RemoteOrder {
    pub binding_id: String,
    pub order_id: OrderId,
    pub client_order_id: Option<ClientOrderId>,
    pub symbol: Symbol,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub status: ExecutionOrderStatus,
    pub quantity: Quantity,
    pub filled_quantity: Quantity,
    pub average_fill_price: Option<Price>,
    pub occurred_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Deserialize)]
struct RemoteOrderWire {
    #[serde(default)]
    binding_id: String,
    order_id: String,
    client_order_id: Option<String>,
    symbol: String,
    side: OrderSide,
    order_type: OrderType,
    status: String,
    quantity: String,
    filled_quantity: String,
    average_fill_price: Option<String>,
    occurred_at_unix_millis: Option<u64>,
}

impl<'de> Deserialize<'de> for RemoteOrder {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let wire = RemoteOrderWire::deserialize(deserializer)?;
        Ok(Self {
            binding_id: wire.binding_id,
            order_id: OrderId::new(wire.order_id).map_err(serde::de::Error::custom)?,
            client_order_id: wire
                .client_order_id
                .map(ClientOrderId::new)
                .transpose()
                .map_err(serde::de::Error::custom)?,
            symbol: Symbol::new(wire.symbol).map_err(serde::de::Error::custom)?,
            side: wire.side,
            order_type: wire.order_type,
            status: remote_status(&wire.status),
            quantity: wire.quantity.parse().map_err(serde::de::Error::custom)?,
            filled_quantity: wire
                .filled_quantity
                .parse()
                .map_err(serde::de::Error::custom)?,
            average_fill_price: wire
                .average_fill_price
                .map(|value| value.parse())
                .transpose()
                .map_err(serde::de::Error::custom)?,
            occurred_at_unix_nanos: wire
                .occurred_at_unix_millis
                .map(|value| UnixNanos::from(value.saturating_mul(1_000_000))),
        })
    }
}

impl Serialize for RemoteOrder {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        #[derive(Serialize)]
        struct Wire<'a> {
            binding_id: &'a str,
            order_id: &'a str,
            client_order_id: Option<&'a str>,
            symbol: &'a str,
            side: OrderSide,
            order_type: OrderType,
            status: &'a str,
            quantity: String,
            filled_quantity: String,
            average_fill_price: Option<String>,
            occurred_at_unix_millis: Option<u64>,
        }
        let status = match self.status {
            ExecutionOrderStatus::Pending => "pending",
            ExecutionOrderStatus::Accepted => "accepted",
            ExecutionOrderStatus::PartiallyFilled => "partially_filled",
            ExecutionOrderStatus::Filled => "filled",
            ExecutionOrderStatus::Canceled => "canceled",
            ExecutionOrderStatus::Rejected => "rejected",
            ExecutionOrderStatus::Expired => "expired",
            ExecutionOrderStatus::Submitting => "submitting",
            ExecutionOrderStatus::CancelRequested => "cancel_requested",
            ExecutionOrderStatus::Unknown => "unknown",
            ExecutionOrderStatus::Failed => "failed",
        };
        Wire {
            binding_id: &self.binding_id,
            order_id: self.order_id.as_str(),
            client_order_id: self.client_order_id.as_ref().map(ClientOrderId::as_str),
            symbol: self.symbol.as_str(),
            side: self.side,
            order_type: self.order_type,
            status,
            quantity: self.quantity.to_string(),
            filled_quantity: self.filled_quantity.to_string(),
            average_fill_price: self.average_fill_price.map(|value| value.to_string()),
            occurred_at_unix_millis: self
                .occurred_at_unix_nanos
                .map(|value| value.get() / 1_000_000),
        }
        .serialize(serializer)
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAuditQuery {
    pub order_id: Option<OrderId>,
    pub remote_order_id: Option<kairos_primitives::RemoteOrderId>,
    pub status: Option<String>,
    pub since_unix_nanos: Option<UnixNanos>,
    pub until_unix_nanos: Option<UnixNanos>,
    pub limit: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAuditEvent {
    pub sequence: Sequence,
    pub order_id: OrderId,
    pub status: ExecutionOrderStatus,
    pub remote_order_id: Option<kairos_primitives::RemoteOrderId>,
    pub occurred_at_unix_nanos: UnixNanos,
    pub reason: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IntentStatus {
    Accepted,
    Planning,
    Planned,
    Executing,
    PartiallyFilled,
    CancelRequested,
    Satisfied,
    Rejected,
    Canceled,
    Expired,
    Failed,
    Compensating,
    ReconciliationRequired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecuteStrategyIntent {
    pub intent_id: IntentId,
    pub strategy_id: String,
    pub launch_id: String,
    pub instance_id: String,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    #[serde(default)]
    pub execution_access_id: Option<ExecutionAccessId>,
    pub account_ids: Vec<AccountId>,
    pub segment_key: SegmentKey,
    pub target_quantity: Quantity,
    pub limit_price: Option<Price>,
    pub source_snapshot_id: Option<String>,
    pub source_event_sequence: Option<Sequence>,
    pub source_event_time_unix_nanos: Option<UnixNanos>,
    pub reason: String,
    pub intent_type: IntentType,
    pub completion_policy: CompletionPolicy,
    pub failure_policy: FailurePolicy,
    pub legs: Vec<IntentLegRequest>,
    pub deadline_unix_nanos: Option<UnixNanos>,
    pub min_edge_bps: Option<u32>,
    pub max_slippage_bps: Option<u32>,
    /// Estimated round-trip fees for a multi-leg execution.  This is an
    /// advisory input used by admission to reject an edge that is only
    /// positive before fees.
    pub estimated_fee_bps: Option<u32>,
    #[serde(default)]
    pub minimum_net_credit: Option<Money>,
    #[serde(default)]
    pub maximum_loss: Option<Money>,
    pub hedge_policy: Option<HedgePolicy>,
    pub order_options: ExecutionOrderOptions,
}

impl Default for ExecuteStrategyIntent {
    fn default() -> Self {
        Self {
            intent_id: IntentId::new("intent:default").expect("valid default intent ID"),
            strategy_id: String::new(),
            launch_id: String::new(),
            instance_id: String::new(),
            instrument_id: InstrumentId::new("instrument:default")
                .expect("valid default instrument ID"),
            market_id: None,
            execution_access_id: None,
            account_ids: Vec::new(),
            segment_key: SegmentKey::new("segment:default").expect("valid default segment key"),
            target_quantity: Quantity::new(0, 0).expect("valid default quantity"),
            limit_price: None,
            source_snapshot_id: None,
            source_event_sequence: None,
            source_event_time_unix_nanos: None,
            reason: String::new(),
            intent_type: IntentType::default(),
            completion_policy: CompletionPolicy::default(),
            failure_policy: FailurePolicy::default(),
            legs: Vec::new(),
            deadline_unix_nanos: None,
            min_edge_bps: None,
            max_slippage_bps: None,
            estimated_fee_bps: None,
            minimum_net_credit: None,
            maximum_loss: None,
            hedge_policy: None,
            order_options: ExecutionOrderOptions::default(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentLegRequest {
    pub leg_id: LegId,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    #[serde(default)]
    pub execution_access_id: Option<ExecutionAccessId>,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub limit_price: Option<Price>,
    pub target_position: bool,
    pub options: ExecutionOrderOptions,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentState {
    pub intent: ExecuteStrategyIntent,
    pub status: IntentStatus,
    pub order_ids: Vec<OrderId>,
    #[serde(default)]
    pub plan: Option<ExecutionPlan>,
    pub completed_quantity: Quantity,
    pub updated_at_unix_nanos: UnixNanos,
    pub reason: String,
    #[serde(default)]
    pub dependency_watermarks: DependencyWatermarks,
    #[serde(default)]
    pub pending_orders: Vec<SubmitOrder>,
    #[serde(default)]
    pub pending_order_due_unix_nanos: BTreeMap<OrderId, UnixNanos>,
    #[serde(default)]
    pub quote_version: u64,
    #[serde(default)]
    pub last_quote_refresh_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub compensation_attempts: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct HedgeRequirement {
    pub intent_id: IntentId,
    pub leader_leg_id: LegId,
    pub hedge_leg_id: LegId,
    pub leader_filled_quantity: Quantity,
    pub hedge_filled_quantity: Quantity,
    pub required_hedge_quantity: Quantity,
    pub unhedged_quantity: Quantity,
    pub max_unhedged_quantity: Quantity,
    pub within_tolerance: bool,
    pub compensation_attempts: u32,
    pub max_compensation_attempts: u32,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct SnapshotWatermark {
    pub generation: Generation,
    pub event_sequence: Sequence,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct DependencyWatermarks {
    #[serde(default)]
    pub account: BTreeMap<String, SnapshotWatermark>,
    pub market: Option<SnapshotWatermark>,
    pub reference: Option<SnapshotWatermark>,
    pub risk: Option<SnapshotWatermark>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentEvent {
    pub intent_id: IntentId,
    #[serde(default)]
    pub event_sequence: Sequence,
    pub status: IntentStatus,
    pub order_ids: Vec<OrderId>,
    pub completed_quantity: Quantity,
    pub occurred_at_unix_nanos: UnixNanos,
    pub reason: String,
    #[serde(default)]
    pub dependency_watermarks: DependencyWatermarks,
}

#[derive(Debug, thiserror::Error)]
pub enum ExecutionError {
    #[error("invalid execution request: {0}")]
    Invalid(String),
    #[error("execution gateway failed: {0}")]
    Gateway(String),
    #[error("provider rejected the execution command: {0}")]
    ProviderRejected(String),
    #[error("execution command outcome is indeterminate: {0}")]
    Indeterminate(String),
    #[error("execution persistence failed: {0}")]
    Persistence(String),
}

pub(crate) fn remote_status(value: &str) -> ExecutionOrderStatus {
    let normalized = value.to_ascii_lowercase();
    if normalized.contains("partial") && normalized.contains("fill") {
        ExecutionOrderStatus::PartiallyFilled
    } else if normalized.contains("fill") {
        ExecutionOrderStatus::Filled
    } else if normalized.contains("cancel") {
        ExecutionOrderStatus::Canceled
    } else if normalized.contains("reject") {
        ExecutionOrderStatus::Rejected
    } else if normalized.contains("expire") {
        ExecutionOrderStatus::Expired
    } else if normalized.contains("submit") || normalized.contains("accept") {
        ExecutionOrderStatus::Accepted
    } else {
        ExecutionOrderStatus::Unknown
    }
}
