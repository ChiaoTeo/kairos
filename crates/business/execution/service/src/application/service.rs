use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use kairos_domain_types::{
    AccountId, ActorId, ClientOrderId, Currency, DurationNanos, FillId, Generation, InstrumentId,
    IntentId, LegId, MarketId, Money, OrderId, PlanId, Price, Quantity, RemoteOrderId, SegmentKey,
    Sequence, Symbol, UnixNanos,
};
use serde::{Deserialize, Serialize};

use super::RemoteOrderUpdate;
use crate::domain::{
    split_quantity, CompletionPolicy, ExecutionFill, ExecutionLeg, ExecutionOrder,
    ExecutionOrderStatus, ExecutionPlan, FailurePolicy, HedgePolicy, IntentType, LegLifecycle,
    MakerExecutionPolicy, OrderSide, OrderType, SplitOrderPolicy,
};

fn typed_intent_id(value: impl Into<String>) -> IntentId {
    IntentId::new(value).expect("validated intent ID")
}

fn completed_quantity(intent: &ExecuteStrategyIntent, mantissa: i64) -> Quantity {
    Quantity::new(mantissa, intent.target_quantity.scale()).expect("completed quantity is valid")
}
use crate::services::persistence::{ExecutionOutboxEntry, ExecutionStateStore};
use kairos_integration::application::ExternalOrderQuery;
use kairos_integration::application::{
    CommandOutcome, DecimalValue as ConnectionDecimalValue, OrderEntryEvent, OrderEntryRequest,
    OrderEntryStatus,
};
use kairos_integration::application::{
    OrderEntryOptions as ConnectionOrderEntryOptions, OrderSide as ConnectionOrderSide,
    OrderType as ConnectionOrderType, TimeInForce,
};
use kairos_integration::blocking::{OrderEntryConnection, OrderEventSource, OrderQueryConnection};
use tracing::{debug, info, warn};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SubmitOrder {
    pub order_id: OrderId,
    pub intent_id: Option<IntentId>,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
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
    pub occurred_at_unix_nanos: Option<UnixNanos>,
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

/// A exchange order observed through a private stream or remote query that could
/// not be associated with a locally submitted order.  It is deliberately
/// persisted instead of being discarded or treated as a transient gateway
/// error: recovery must be able to inspect and resolve it after a restart.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct UnknownRemoteOrder {
    pub remote_order_id: kairos_domain_types::RemoteOrderId,
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
    pub remote_order_id: Option<kairos_domain_types::RemoteOrderId>,
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
    pub remote_order_id: Option<kairos_domain_types::RemoteOrderId>,
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
    /// advisory input used by preflight to reject an edge that is only
    /// positive before fees.
    pub estimated_fee_bps: Option<u32>,
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

pub struct ExecutionApplication {
    actor_id: String,
    generation: u64,
    event_sequence: u64,
    orders: BTreeMap<crate::domain::OrderId, ExecutionOrder>,
    events: Vec<ExecutionEvent>,
    pending_events: Vec<ExecutionEvent>,
    fills: Vec<ExecutionFill>,
    intents: BTreeMap<String, IntentState>,
    intent_events: Vec<IntentEvent>,
    pending_intent_events: Vec<IntentEvent>,
    intent_idempotency: BTreeMap<String, String>,
    unknown_remote_orders: BTreeMap<String, UnknownRemoteOrder>,
    exchange_event_watermark_unix_nanos: u64,
    order_entry: Option<Box<dyn OrderEntryConnection>>,
    order_query: Option<Box<dyn OrderQueryConnection>>,
    execution_stream: Option<Box<dyn OrderEventSource>>,
    store: Option<Box<dyn ExecutionStateStore>>,
    preflight: Option<Box<dyn super::ExecutionPreflight>>,
    live_trading: bool,
    live_confirmed: bool,
}

impl ExecutionApplication {
    /// Advance the replay business clock and its composition-owned
    /// dependencies.  The application remains the state owner; concrete
    /// Account/Risk calls stay behind the preflight boundary.
    pub fn advance_time(&mut self, event_time_unix_nanos: u64) -> Result<(), ExecutionError> {
        if let Some(preflight) = self.preflight.as_mut() {
            preflight
                .advance_time(event_time_unix_nanos)
                .map_err(ExecutionError::Invalid)?;
        }
        Ok(())
    }
    pub fn with_dependencies(
        actor_id: impl Into<String>,
        order_entry: Option<Box<dyn OrderEntryConnection>>,
        store: Option<Box<dyn ExecutionStateStore>>,
    ) -> Result<Self, ExecutionError> {
        Self::with_dependencies_and_query(actor_id, order_entry, None, store)
    }

    pub fn with_dependencies_and_query(
        actor_id: impl Into<String>,
        order_entry: Option<Box<dyn OrderEntryConnection>>,
        order_query: Option<Box<dyn OrderQueryConnection>>,
        store: Option<Box<dyn ExecutionStateStore>>,
    ) -> Result<Self, ExecutionError> {
        Self::with_dependencies_and_query_and_stream(
            actor_id,
            order_entry,
            order_query,
            None,
            store,
        )
    }

    pub fn with_dependencies_and_query_and_stream(
        actor_id: impl Into<String>,
        order_entry: Option<Box<dyn OrderEntryConnection>>,
        order_query: Option<Box<dyn OrderQueryConnection>>,
        execution_stream: Option<Box<dyn OrderEventSource>>,
        store: Option<Box<dyn ExecutionStateStore>>,
    ) -> Result<Self, ExecutionError> {
        let actor_id = actor_id.into();
        if actor_id.trim().is_empty() {
            return Err(ExecutionError::Invalid("actor_id is required".into()));
        }
        let mut application = Self {
            actor_id,
            generation: 0,
            event_sequence: 0,
            orders: BTreeMap::new(),
            events: Vec::new(),
            pending_events: Vec::new(),
            fills: Vec::new(),
            intents: BTreeMap::new(),
            intent_events: Vec::new(),
            pending_intent_events: Vec::new(),
            intent_idempotency: BTreeMap::new(),
            unknown_remote_orders: BTreeMap::new(),
            exchange_event_watermark_unix_nanos: 0,
            order_entry,
            order_query,
            execution_stream,
            store,
            preflight: None,
            live_trading: false,
            live_confirmed: false,
        };
        if let Some(store) = application.store.as_mut() {
            if let Some(snapshot) = store.load().map_err(ExecutionError::Persistence)? {
                application.generation = snapshot.generation.get();
                application.event_sequence = snapshot.event_sequence.get();
                application.orders = snapshot
                    .orders
                    .into_iter()
                    .map(|order| (order.order_id.clone(), order))
                    .collect();
                application.events = snapshot.events;
                application.pending_events = application.events.clone();
                application.fills = snapshot.fills;
                application.intents = snapshot
                    .intents
                    .into_iter()
                    .map(|intent| (intent.intent.intent_id.to_string(), intent))
                    .collect();
                application.intent_events = snapshot.intent_events;
                application.pending_intent_events = application.intent_events.clone();
                application.intent_idempotency = snapshot.intent_idempotency;
                application.unknown_remote_orders = snapshot
                    .unknown_remote_orders
                    .into_iter()
                    .map(|order| (order.remote_order_id.to_string(), order))
                    .collect();
                application.exchange_event_watermark_unix_nanos =
                    snapshot.exchange_event_watermark_unix_nanos.get();
                info!(
                    event = "execution_state_restored",
                    component = "execution",
                    generation = application.generation,
                    event_sequence = application.event_sequence,
                    order_count = application.orders.len(),
                    intent_count = application.intents.len(),
                    fill_count = application.fills.len(),
                    "execution state restored from persistence"
                );
            }
        }
        Ok(application)
    }

    pub fn remote_open_orders(
        &mut self,
        query: RemoteOrderQuery,
    ) -> Result<Vec<RemoteOrder>, ExecutionError> {
        info!(event = "remote_open_orders_query_started", component = "execution", order_id = ?query.order_id, symbol = ?query.symbol, "remote open-orders query started");
        self.order_query
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("remote order query is not configured".into()))?
            .open_orders(&ExternalOrderQuery {
                binding_id: query.binding_id.clone(),
                symbol: query.symbol.clone(),
                order_id: query.order_id.clone(),
                limit: query.limit,
                since_unix_millis: query.since_unix_nanos,
            })
            .map(|orders| {
                let count = orders.len();
                info!(event = "remote_open_orders_query_completed", component = "execution", count, "remote open-orders query completed");
                orders.into_iter().map(remote_order).collect()
            })
            .map_err(|error| { warn!(event = "remote_open_orders_query_failed", component = "execution", error = %error, "remote open-orders query failed"); ExecutionError::Gateway(error.to_string()) })
    }

    pub fn remote_history(
        &mut self,
        query: RemoteOrderQuery,
    ) -> Result<Vec<RemoteOrder>, ExecutionError> {
        self.order_query
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("remote order query is not configured".into()))?
            .order_history(&ExternalOrderQuery {
                binding_id: query.binding_id.clone(),
                symbol: query.symbol.clone(),
                order_id: query.order_id.clone(),
                limit: query.limit,
                since_unix_millis: query.since_unix_nanos,
            })
            .map(|orders| orders.into_iter().map(remote_order).collect())
            .map_err(|error| ExecutionError::Gateway(error.to_string()))
    }

    pub fn remote_detail(
        &mut self,
        query: RemoteOrderQuery,
    ) -> Result<Option<RemoteOrder>, ExecutionError> {
        self.order_query
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("remote order query is not configured".into()))?
            .order_detail(&ExternalOrderQuery {
                binding_id: query.binding_id.clone(),
                symbol: query.symbol.clone(),
                order_id: query.order_id.clone(),
                limit: query.limit,
                since_unix_millis: query.since_unix_nanos,
            })
            .map(|order| order.map(remote_order))
            .map_err(|error| ExecutionError::Gateway(error.to_string()))
    }

    /// Reconcile locally active/unknown orders with the exchange query surface.
    /// Private streams are not guaranteed to deliver events during a
    /// disconnect, so recovery must compare the durable local journal with
    /// the exchange's open-order and history views.
    pub fn reconcile_remote_orders(
        &mut self,
        mut query: RemoteOrderQuery,
    ) -> Result<usize, ExecutionError> {
        if query.since_unix_nanos.is_none() && self.exchange_event_watermark_unix_nanos > 0 {
            query.since_unix_nanos = Some(
                self.exchange_event_watermark_unix_nanos
                    .saturating_sub(30_000_000_000)
                    .into(),
            );
        }
        let mut remote = self.remote_open_orders(query.clone())?;
        remote.extend(self.remote_history(query)?);
        remote.sort_by(|left, right| left.order_id.cmp(&right.order_id));
        remote.dedup_by(|left, right| left.order_id == right.order_id);

        let mut changed = 0;
        for remote_order in remote {
            let local = self
                .orders
                .values()
                .find(|order| {
                    remote_order.order_id == order.order_id.as_str()
                        || remote_order
                            .client_order_id
                            .as_ref()
                            .is_some_and(|client_id| client_id.as_str() == order.order_id.as_str())
                        || order.remote_order_id.as_deref() == Some(remote_order.order_id.as_str())
                })
                .cloned();
            let Some(local) = local else {
                self.record_unknown_remote_order(&RemoteOrderUpdate {
                    order_id: remote_order.order_id.clone(),
                    symbol: remote_order.symbol,
                    status: remote_order.status,
                    fill_quantity: Some(remote_order.filled_quantity),
                    fill_price: remote_order.average_fill_price,
                    execution_id: None,
                    fee_currency: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: remote_order
                        .occurred_at_unix_nanos
                        .unwrap_or_else(|| now_nanos().into()),
                    reason: "remote query found an order without a local journal entry".into(),
                })?;
                changed += 1;
                continue;
            };

            let mut reconciled_status = remote_order.status;
            let mut reconciliation_reason = "reconciled from remote order query".to_string();

            // The private stream can be disconnected after the exchange has
            // accepted or filled an order.  A remote order query returns a
            // cumulative fill quantity, so recover only the delta that is
            // missing from the local journal.  This keeps recovery idempotent
            // even when the same order appears in both open-orders and
            // history, or when the query window overlaps a previous recovery.
            let remote_filled = Quantity::new(
                remote_order.filled_quantity.mantissa(),
                remote_order.filled_quantity.scale(),
            )
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
            match remote_filled {
                remote_filled if remote_filled > local.filled_quantity => {
                    let delta = remote_filled
                        .checked_sub(local.filled_quantity)
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
                    let price = remote_order
                        .average_fill_price
                        .map(|value| (value.mantissa(), value.scale()));
                    let price = price.or_else(|| {
                        local
                            .limit_price
                            .map(|value| (value.mantissa(), value.scale()))
                    });
                    if let Some(price) = price {
                        let fill_result = self.record_fill(ExecutionFillReport {
                            fill_id: FillId::new(format!(
                                "reconcile:{}:{}:{}",
                                remote_order.order_id,
                                remote_filled,
                                remote_filled.scale()
                            ))
                            .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                            order_id: local.order_id.clone(),
                            quantity: delta,
                            price: Price::new(price.0, price.1)
                                .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                            fee: Money::ZERO,
                            occurred_at_unix_nanos: remote_order.occurred_at_unix_nanos,
                        });
                        match fill_result {
                            Ok(_) => changed += 1,
                            Err(error) => {
                                reconciled_status = ExecutionOrderStatus::Unknown;
                                reconciliation_reason =
                                    format!("remote cumulative fill could not be applied: {error}");
                            }
                        }
                    } else {
                        reconciled_status = ExecutionOrderStatus::Unknown;
                        reconciliation_reason =
                            "remote fill has no average price; manual reconciliation required"
                                .into();
                    }
                }
                remote_filled if remote_filled < local.filled_quantity => {
                    reconciled_status = ExecutionOrderStatus::Unknown;
                    reconciliation_reason = format!(
                        "remote cumulative fill {} is behind local fill {}; manual reconciliation required",
                        remote_filled,
                        local.filled_quantity
                    );
                }
                _ => {}
            }

            let local =
                self.orders.get(&local.order_id).cloned().ok_or_else(|| {
                    ExecutionError::Invalid("reconciled order disappeared".into())
                })?;
            if local.status != reconciled_status
                || local.remote_order_id.as_deref() != Some(remote_order.order_id.as_str())
            {
                let mut next = local;
                next.remote_order_id = Some(
                    crate::domain::RemoteOrderId::new(remote_order.order_id.to_string())
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                );
                next.status = reconciled_status;
                next.updated_at_unix_nanos = remote_order
                    .occurred_at_unix_nanos
                    .unwrap_or_else(|| now_nanos().into());
                next.reason = reconciliation_reason;
                self.orders.insert(next.order_id.clone(), next.clone());
                self.commit(ExecutionEvent {
                    order_id: next.order_id.clone(),
                    intent_id: next.intent_id.clone(),
                    plan_id: next.plan_id.clone(),
                    leg_id: next.leg_id.clone(),
                    status: next.status,
                    remote_order_id: next.remote_order_id.clone(),
                    occurred_at_unix_nanos: next.updated_at_unix_nanos,
                    reason: next.reason.clone(),
                    fill_id: None,
                    filled_quantity: None,
                })?;
                changed += 1;
            }
        }
        Ok(changed)
    }

    pub fn next_remote_execution_event(
        &mut self,
    ) -> Result<Option<RemoteOrderUpdate>, ExecutionError> {
        self.execution_stream
            .as_mut()
            .ok_or_else(|| ExecutionError::Gateway("execution stream is not configured".into()))?
            .try_next_order_event()
            .map(|event| event.map(|envelope| remote_execution_event(envelope.payload)))
            .map_err(|error| ExecutionError::Gateway(error.to_string()))
    }

    /// Transfer ownership of the provider stream to the runtime stream
    /// consumer.  HTTP requests must not pull provider events themselves.
    pub fn take_execution_stream(&mut self) -> Option<Box<dyn OrderEventSource>> {
        self.execution_stream.take()
    }

    /// Transfer the order-entry connection to the composition-owned gateway
    /// worker.  Execution state does not own the provider connection after
    /// process startup.
    pub fn take_order_entry(&mut self) -> Option<Box<dyn OrderEntryConnection>> {
        self.order_entry.take()
    }

    pub fn install_order_entry(&mut self, connection: Box<dyn OrderEntryConnection>) {
        self.order_entry = Some(connection);
    }

    /// Transfer ownership of the provider order-query connection to the
    /// composition-owned query worker.
    pub fn take_order_query(&mut self) -> Option<Box<dyn OrderQueryConnection>> {
        self.order_query.take()
    }

    pub fn install_order_query(&mut self, connection: Box<dyn OrderQueryConnection>) {
        self.order_query = Some(connection);
    }

    pub fn has_order_query(&self) -> bool {
        self.order_query.is_some()
    }

    /// Pull one provider update and reconcile it into the local execution
    /// journal.  The provider order id is matched against the stored exchange
    /// order id; this keeps the integration event provider-neutral while the
    /// execution application remains the owner of lifecycle state.
    pub fn consume_remote_execution_event(
        &mut self,
    ) -> Result<Option<(RemoteOrderUpdate, ExecutionOrder)>, ExecutionError> {
        let Some(event) = self.next_remote_execution_event()? else {
            return Ok(None);
        };
        let applied = self.apply_remote_execution_event(event.clone())?;
        Ok(Some((event, applied)))
    }

    /// Apply one normalized exchange fact received from the private order stream.
    /// The stream consumer owns subscription and delivery; this method only
    /// mutates Execution-owned lifecycle state.
    pub fn apply_remote_execution_event(
        &mut self,
        event: RemoteOrderUpdate,
    ) -> Result<ExecutionOrder, ExecutionError> {
        info!(event = "remote_execution_event_received", component = "execution", remote_order_id = %event.order_id, status = ?event.status, "remote execution event received");
        self.exchange_event_watermark_unix_nanos = self
            .exchange_event_watermark_unix_nanos
            .max(event.occurred_at_unix_nanos.get());
        let local = self
            .orders
            .values()
            .find(|order| {
                order.order_id.as_str() == event.order_id.as_str()
                    || order.remote_order_id.as_deref() == Some(event.order_id.as_str())
            })
            .cloned()
            .ok_or_else(|| {
                let _ = self.record_unknown_remote_order(&event);
                ExecutionError::Invalid(format!(
                    "remote execution references unknown order: {}",
                    event.order_id
                ))
            })?;
        if let (Some(quantity), Some(price)) = (&event.fill_quantity, &event.fill_price) {
            let quantity = parse_decimal(&quantity.to_string())?;
            let price = parse_decimal(&price.to_string())?;
            let fee = event
                .fee_amount
                .as_ref()
                .map(|value| parse_decimal(&value.to_string()))
                .transpose()?
                .map(|value| {
                    Money::new(value.0, value.1)
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))
                })
                .transpose()?
                .unwrap_or(Money::ZERO);
            let fill_id = event.execution_id.clone().unwrap_or(
                FillId::new(format!(
                    "remote:{}:{}",
                    event.order_id, event.occurred_at_unix_nanos
                ))
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
            );
            let fill = self.record_fill(ExecutionFillReport {
                fill_id,
                order_id: local.order_id.clone(),
                quantity: Quantity::new(quantity.0, quantity.1)
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                price: Price::new(price.0, price.1)
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                fee,
                occurred_at_unix_nanos: Some(event.occurred_at_unix_nanos),
            })?;
            return Ok(fill);
        }
        let mut next = local;
        next.remote_order_id = Some(
            crate::domain::RemoteOrderId::new(event.order_id.to_string())
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
        );
        next.updated_at_unix_nanos =
            crate::domain::UnixNanos::new(event.occurred_at_unix_nanos.get());
        next.reason = event.reason.clone();
        next.status = event.status;
        self.orders.insert(next.order_id.clone(), next.clone());
        self.commit(ExecutionEvent {
            order_id: next.order_id.clone(),
            intent_id: next.intent_id.clone(),
            plan_id: next.plan_id.clone(),
            leg_id: next.leg_id.clone(),
            status: next.status,
            remote_order_id: next.remote_order_id.clone(),
            occurred_at_unix_nanos: next.updated_at_unix_nanos,
            reason: next.reason.clone(),
            fill_id: None,
            filled_quantity: None,
        })?;
        info!(event = "remote_execution_event_reconciled", component = "execution", order_id = %next.order_id, status = ?next.status, "remote execution event reconciled");
        Ok(next)
    }

    pub fn snapshot(&self) -> ExecutionSnapshot {
        ExecutionSnapshot {
            actor_id: ActorId::new(self.actor_id.clone()).expect("validated actor ID"),
            generation: self.generation.into(),
            event_sequence: self.event_sequence.into(),
            orders: self.orders.values().cloned().collect(),
            events: self.events.clone(),
            fills: self.fills.clone(),
            intents: self.intents.values().cloned().collect(),
            intent_events: self.intent_events.clone(),
            intent_idempotency: self.intent_idempotency.clone(),
            unknown_remote_orders: self.unknown_remote_orders.values().cloned().collect(),
            exchange_event_watermark_unix_nanos: self.exchange_event_watermark_unix_nanos.into(),
        }
    }

    pub fn unknown_remote_orders(&self) -> Vec<UnknownRemoteOrder> {
        self.unknown_remote_orders.values().cloned().collect()
    }

    /// Mark a previously unknown remote order as resolved once an operator or
    /// reconciliation process has established its local order association.
    pub fn resolve_unknown_remote_order(
        &mut self,
        remote_order_id: &str,
        resolution: UnknownRemoteOrderResolution,
        reason: impl Into<String>,
    ) -> Result<(), ExecutionError> {
        let Some(order) = self.unknown_remote_orders.get_mut(remote_order_id) else {
            return Err(ExecutionError::Invalid(format!(
                "unknown remote order does not exist: {remote_order_id}"
            )));
        };
        order.resolution = resolution;
        order.reason = reason.into();
        order.last_seen_at_unix_nanos = now_nanos().into();
        self.persist_snapshot()
    }

    /// Link an unknown exchange order to a locally submitted order after
    /// reconciliation has established the association. If the remote fact
    /// includes a fill, apply it through the normal idempotent fill path.
    pub fn link_unknown_remote_order(
        &mut self,
        remote_order_id: &str,
        local_order_id: &str,
    ) -> Result<ExecutionOrder, ExecutionError> {
        let unknown = self
            .unknown_remote_orders
            .get(remote_order_id)
            .cloned()
            .ok_or_else(|| {
                ExecutionError::Invalid(format!(
                    "unknown remote order does not exist: {remote_order_id}"
                ))
            })?;
        let mut local = self.orders.get(local_order_id).cloned().ok_or_else(|| {
            ExecutionError::Invalid(format!("unknown local order: {local_order_id}"))
        })?;
        local.remote_order_id = Some(
            crate::domain::RemoteOrderId::new(remote_order_id.to_owned())
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
        );
        local.status = unknown.status;
        local.updated_at_unix_nanos =
            crate::domain::UnixNanos::new(unknown.last_seen_at_unix_nanos.get());
        local.reason = "linked from unknown remote order reconciliation".into();
        self.orders.insert(local.order_id.clone(), local.clone());
        if let Some(entry) = self.unknown_remote_orders.get_mut(remote_order_id) {
            entry.resolution = UnknownRemoteOrderResolution::LinkedToLocalOrder;
            entry.reason = format!("linked to local order {local_order_id}");
        }
        self.commit(ExecutionEvent {
            order_id: OrderId::new(local_order_id).expect("validated local order ID"),
            intent_id: local.intent_id.clone(),
            plan_id: local.plan_id.clone(),
            leg_id: local.leg_id.clone(),
            status: local.status,
            remote_order_id: local.remote_order_id.clone(),
            occurred_at_unix_nanos: unknown.last_seen_at_unix_nanos,
            reason: local.reason.clone(),
            fill_id: None,
            filled_quantity: None,
        })?;
        if let (Some(quantity), Some(price)) = (unknown.fill_quantity, unknown.fill_price) {
            return self.record_fill(ExecutionFillReport {
                fill_id: unknown.execution_id.unwrap_or(
                    FillId::new(format!("remote:{remote_order_id}:{local_order_id}"))
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                ),
                order_id: OrderId::new(local_order_id.to_owned())
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                quantity,
                price,
                fee: unknown.fee_amount.unwrap_or(Money::ZERO),
                occurred_at_unix_nanos: Some(unknown.last_seen_at_unix_nanos),
            });
        }
        self.persist_snapshot()?;
        Ok(local)
    }

    pub fn drain_events(&mut self) -> Vec<ExecutionEvent> {
        std::mem::take(&mut self.pending_events)
    }

    pub fn intents(&self) -> Vec<IntentState> {
        self.intents.values().cloned().collect()
    }

    pub fn intent(&self, intent_id: &str) -> Option<IntentState> {
        self.intents.get(intent_id).cloned()
    }

    pub fn intent_events(&self, intent_id: Option<&str>) -> Vec<IntentEvent> {
        self.intent_events
            .iter()
            .filter(|event| intent_id.is_none_or(|id| event.intent_id == id))
            .cloned()
            .collect()
    }

    /// Paged event consumption for strategy/system recovery.  The returned
    /// sequence is strictly after `after_sequence`; callers can compare the
    /// first sequence with their watermark and fall back to the snapshot when
    /// a gap is detected.
    pub fn intent_events_after(
        &self,
        intent_id: Option<&str>,
        after_sequence: u64,
        limit: Option<usize>,
    ) -> Vec<IntentEvent> {
        let limit = limit.unwrap_or(usize::MAX);
        self.intent_events
            .iter()
            .filter(|event| {
                event.event_sequence.get() > after_sequence
                    && intent_id.is_none_or(|id| event.intent_id.as_str() == id)
            })
            .take(limit)
            .cloned()
            .collect()
    }

    /// Return hedge progress from actual fills.  This deliberately reports
    /// the required hedge quantity instead of submitting a synthetic order;
    /// the caller/scheduler can then apply exchange, price and risk checks before
    /// creating a compensating child order.
    pub fn hedge_requirement(
        &self,
        intent_id: &str,
    ) -> Result<Option<HedgeRequirement>, ExecutionError> {
        let Some(state) = self.intents.get(intent_id) else {
            return Err(ExecutionError::Invalid("unknown intent".into()));
        };
        let Some(policy) = state.intent.hedge_policy.as_ref() else {
            return Ok(None);
        };
        let Some(plan) = state.plan.as_ref() else {
            return Ok(None);
        };
        let filled_for = |leg_id: &str| -> Result<Quantity, ExecutionError> {
            let Some(leg) = plan.legs.iter().find(|leg| leg.leg_id == leg_id) else {
                return Err(ExecutionError::Invalid(format!(
                    "hedge leg is missing from execution plan: {leg_id}"
                )));
            };
            leg.order_ids
                .iter()
                .try_fold(Quantity::ZERO, |total, order_id| {
                    total
                        .checked_add(
                            self.orders
                                .get(order_id.as_str())
                                .map(|order| order.filled_quantity)
                                .unwrap_or(Quantity::ZERO),
                        )
                        .map_err(|_| ExecutionError::Invalid("hedge fill quantity overflow".into()))
                })
        };
        let leader = filled_for(&policy.leader_leg_id)?;
        let hedge = filled_for(&policy.hedge_leg_id)?;
        let required = policy
            .required_hedge_quantity(leader, Quantity::ZERO)
            .map_err(ExecutionError::Invalid)?;
        let unhedged = if hedge >= required {
            Quantity::ZERO
        } else {
            required
                .checked_sub(hedge)
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?
        };
        Ok(Some(HedgeRequirement {
            intent_id: typed_intent_id(intent_id),
            leader_leg_id: policy.leader_leg_id.clone(),
            hedge_leg_id: policy.hedge_leg_id.clone(),
            leader_filled_quantity: leader,
            hedge_filled_quantity: hedge,
            required_hedge_quantity: required,
            unhedged_quantity: unhedged,
            max_unhedged_quantity: policy.max_unhedged_quantity,
            within_tolerance: unhedged <= policy.max_unhedged_quantity,
            compensation_attempts: state.compensation_attempts,
            max_compensation_attempts: policy.max_compensation_attempts,
        }))
    }

    /// Submit a compensating hedge only for the quantity that is still
    /// missing after considering both fills and already-active hedge orders.
    /// This is deliberately fill-driven: a large original hedge target never
    /// causes an additional order unless the leader leg actually fills.
    fn maybe_submit_compensating_hedge(&mut self, intent_id: &str) -> Result<(), ExecutionError> {
        let Some(state) = self.intents.get(intent_id).cloned() else {
            return Ok(());
        };
        let Some(policy) = state.intent.hedge_policy.clone() else {
            return Ok(());
        };
        let Some(plan) = state.plan.clone() else {
            return Ok(());
        };
        let requirement = self
            .hedge_requirement(intent_id)?
            .ok_or_else(|| ExecutionError::Invalid("hedge policy could not be evaluated".into()))?;
        let Some(hedge_leg) = plan
            .legs
            .iter()
            .find(|leg| leg.leg_id == policy.hedge_leg_id)
        else {
            return Err(ExecutionError::Invalid("hedge leg is missing".into()));
        };
        let active_quantity = hedge_leg
            .order_ids
            .iter()
            .filter_map(|order_id| self.orders.get(order_id.as_str()))
            .filter(|order| !order.status.terminal())
            .try_fold(0_i64, |total, order| {
                total.checked_add(
                    order
                        .quantity
                        .mantissa()
                        .saturating_sub(order.filled_quantity.mantissa()),
                )
            })
            .ok_or_else(|| ExecutionError::Invalid("active hedge quantity overflow".into()))?;
        let missing = requirement
            .required_hedge_quantity
            .mantissa()
            .saturating_sub(requirement.hedge_filled_quantity.mantissa())
            .saturating_sub(active_quantity);
        if missing <= requirement.max_unhedged_quantity.mantissa() {
            return Ok(());
        }
        if state.compensation_attempts >= policy.max_compensation_attempts {
            self.commit_intent(IntentEvent {
                intent_id: typed_intent_id(intent_id),
                event_sequence: 0.into(),
                status: IntentStatus::ReconciliationRequired,
                order_ids: Vec::new(),
                completed_quantity: state.completed_quantity,
                occurred_at_unix_nanos: now_nanos().into(),
                reason: format!(
                    "compensation breaker opened after {} attempts; unhedged={missing}",
                    state.compensation_attempts
                ),
                dependency_watermarks: state.dependency_watermarks,
            })?;
            return Ok(());
        }
        if !policy.compensate_on_failure {
            let current = self
                .intents
                .get(intent_id)
                .cloned()
                .ok_or_else(|| ExecutionError::Invalid("hedge intent disappeared".into()))?;
            self.commit_intent(IntentEvent {
                intent_id: typed_intent_id(intent_id),
                event_sequence: 0.into(),
                status: IntentStatus::ReconciliationRequired,
                order_ids: Vec::new(),
                completed_quantity: current.completed_quantity,
                occurred_at_unix_nanos: now_nanos().into(),
                reason: "hedge exposure exceeds tolerance and compensation is disabled".into(),
                dependency_watermarks: current.dependency_watermarks,
            })?;
            return Ok(());
        }
        let template_id = hedge_leg
            .order_ids
            .first()
            .ok_or_else(|| ExecutionError::Invalid("hedge leg has no order template".into()))?;
        let template = self
            .orders
            .get(template_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("hedge order template is missing".into()))?;
        let order_id = format!(
            "{}:compensate:{}",
            intent_id,
            requirement.required_hedge_quantity.mantissa()
        );
        if self.orders.contains_key(order_id.as_str()) {
            return Ok(());
        }
        let options = state
            .intent
            .legs
            .iter()
            .find(|leg| leg.leg_id == policy.hedge_leg_id)
            .map(|leg| leg.options.clone())
            .unwrap_or_else(|| state.intent.order_options.clone());
        let request = SubmitOrder {
            order_id: OrderId::new(order_id).expect("validated compensating order ID"),
            intent_id: Some(IntentId::new(intent_id).expect("validated intent ID")),
            account_id: template.account_id.clone(),
            segment_key: template.segment_key.clone(),
            instrument_id: template.instrument_id.clone(),
            market_id: template.market_id.clone(),
            side: template.side,
            order_type: template.order_type,
            quantity: Quantity::new(missing, template.quantity.scale())
                .expect("validated compensating quantity"),
            limit_price: template.limit_price,
            options,
            submitted_at_unix_nanos: Some(template.submitted_at_unix_nanos),
        };
        if let Some(current) = self.intents.get_mut(intent_id) {
            current.compensation_attempts = current.compensation_attempts.saturating_add(1);
        }
        match self.submit(request) {
            Ok(order) => {
                self.attach_plan_order(intent_id, &policy.hedge_leg_id, &order.order_id)?;
                let state =
                    self.intents.get(intent_id).cloned().ok_or_else(|| {
                        ExecutionError::Invalid("hedge intent disappeared".into())
                    })?;
                self.commit_intent(IntentEvent {
                    intent_id: typed_intent_id(intent_id),
                    event_sequence: 0.into(),
                    status: IntentStatus::Compensating,
                    order_ids: vec![order.order_id.clone()],
                    completed_quantity: state.completed_quantity,
                    occurred_at_unix_nanos: now_nanos().into(),
                    reason: "leader fill exceeded active hedge quantity".into(),
                    dependency_watermarks: state.dependency_watermarks,
                })?;
            }
            Err(error) => {
                self.commit_intent(IntentEvent {
                    intent_id: typed_intent_id(intent_id),
                    event_sequence: 0.into(),
                    status: IntentStatus::ReconciliationRequired,
                    order_ids: Vec::new(),
                    completed_quantity: state.completed_quantity,
                    occurred_at_unix_nanos: now_nanos().into(),
                    reason: format!("compensating hedge failed: {error}"),
                    dependency_watermarks: state.dependency_watermarks,
                })?;
            }
        }
        Ok(())
    }

    pub fn drain_intent_events(&mut self) -> Vec<IntentEvent> {
        std::mem::take(&mut self.pending_intent_events)
    }

    pub fn submit_intent(
        &mut self,
        intent: ExecuteStrategyIntent,
    ) -> Result<IntentState, ExecutionError> {
        info!(event = "intent_received", component = "execution", intent_id = %intent.intent_id, strategy_id = %intent.strategy_id, account_count = intent.account_ids.len(), "strategy intent received");
        if intent.intent_id.as_str().trim().is_empty()
            || intent.strategy_id.trim().is_empty()
            || intent.launch_id.trim().is_empty()
            || intent.instance_id.trim().is_empty()
            || intent.instrument_id.as_str().trim().is_empty()
            || intent.segment_key.as_str().trim().is_empty()
        {
            return Err(ExecutionError::Invalid(
                "intent identity is required".into(),
            ));
        }
        if intent.account_ids.is_empty()
            || intent
                .account_ids
                .iter()
                .any(|id| id.as_str().trim().is_empty())
        {
            return Err(ExecutionError::Invalid(
                "intent must target at least one account".into(),
            ));
        }
        if intent.target_quantity.mantissa() < 0 {
            return Err(ExecutionError::Invalid(
                "intent target quantity cannot be negative".into(),
            ));
        }
        if intent.min_edge_bps.is_some_and(|value| value > 1_000_000)
            || intent
                .max_slippage_bps
                .is_some_and(|value| value > 1_000_000)
            || intent
                .estimated_fee_bps
                .is_some_and(|value| value > 1_000_000)
        {
            return Err(ExecutionError::Invalid(
                "intent execution constraints are out of range".into(),
            ));
        }
        intent
            .hedge_policy
            .as_ref()
            .map(HedgePolicy::validate)
            .transpose()
            .map_err(ExecutionError::Invalid)?;
        intent
            .order_options
            .split
            .as_ref()
            .map(SplitOrderPolicy::validate)
            .transpose()
            .map_err(ExecutionError::Invalid)?;
        intent
            .order_options
            .maker
            .as_ref()
            .map(MakerExecutionPolicy::validate)
            .transpose()
            .map_err(ExecutionError::Invalid)?;
        if intent.intent_type == IntentType::PairArbitrage {
            if intent.legs.len() < 2
                || !intent.legs.iter().any(|leg| leg.side == OrderSide::Buy)
                || !intent.legs.iter().any(|leg| leg.side == OrderSide::Sell)
            {
                return Err(ExecutionError::Invalid(
                    "pair arbitrage requires at least one buy leg and one sell leg".into(),
                ));
            }
            if let Some(policy) = intent.hedge_policy.as_ref() {
                if !intent
                    .legs
                    .iter()
                    .any(|leg| leg.leg_id == policy.leader_leg_id)
                    || !intent
                        .legs
                        .iter()
                        .any(|leg| leg.leg_id == policy.hedge_leg_id)
                {
                    return Err(ExecutionError::Invalid(
                        "hedge policy references a leg outside the pair plan".into(),
                    ));
                }
            }
        }
        for leg in &intent.legs {
            leg.options
                .split
                .as_ref()
                .map(SplitOrderPolicy::validate)
                .transpose()
                .map_err(ExecutionError::Invalid)?;
            leg.options
                .maker
                .as_ref()
                .map(MakerExecutionPolicy::validate)
                .transpose()
                .map_err(ExecutionError::Invalid)?;
        }
        if let Some(existing) = self.intents.get(intent.intent_id.as_str()) {
            debug!(event = "intent_idempotent_replay", component = "execution", intent_id = %intent.intent_id, status = ?existing.status, "existing intent returned without creating a duplicate");
            return Ok(existing.clone());
        }
        let planned_orders = self
            .preflight
            .as_mut()
            .ok_or_else(|| ExecutionError::Invalid("execution preflight is not configured".into()))?
            .plan_intent(&intent)
            .map_err(ExecutionError::Invalid)?;
        let planned_orders = expand_child_orders(&intent, planned_orders)?;
        let business_now = intent
            .source_event_time_unix_nanos
            .map(UnixNanos::get)
            .unwrap_or_else(now_nanos);
        if planned_orders.is_empty() {
            let now = business_now;
            let state = IntentState {
                intent: intent.clone(),
                status: IntentStatus::Satisfied,
                order_ids: Vec::new(),
                plan: None,
                completed_quantity: intent.target_quantity,
                updated_at_unix_nanos: now.into(),
                reason: "target position already satisfied".into(),
                dependency_watermarks: self
                    .preflight
                    .as_ref()
                    .map(|preflight| preflight.dependency_watermarks())
                    .unwrap_or_default(),
                pending_orders: Vec::new(),
                pending_order_due_unix_nanos: BTreeMap::new(),
                quote_version: 0,
                last_quote_refresh_unix_nanos: None,
                compensation_attempts: 0,
            };
            self.intents
                .insert(intent.intent_id.to_string(), state.clone());
            self.commit_intent(IntentEvent {
                intent_id: intent.intent_id.clone(),
                event_sequence: 0.into(),
                status: IntentStatus::Satisfied,
                order_ids: Vec::new(),
                completed_quantity: state.completed_quantity,
                occurred_at_unix_nanos: now.into(),
                reason: state.reason.clone(),
                dependency_watermarks: state.dependency_watermarks.clone(),
            })?;
            info!(event = "intent_satisfied", component = "execution", intent_id = %state.intent.intent_id, reason = %state.reason, "intent already satisfied");
            return Ok(state);
        }
        if planned_orders.iter().any(|order| {
            order
                .intent_id
                .as_ref()
                .is_none_or(|id| id.as_str() != intent.intent_id.as_str())
                || !intent.account_ids.iter().any(|id| id == &order.account_id)
        }) {
            return Err(ExecutionError::Invalid(
                "intent plan contains an order outside its intent accounts".into(),
            ));
        }
        let now = business_now;
        let plan = build_single_intent_plan(&intent, &planned_orders)?;
        let state = IntentState {
            intent: intent.clone(),
            status: IntentStatus::Accepted,
            order_ids: Vec::new(),
            plan: Some(plan),
            completed_quantity: completed_quantity(&intent, 0),
            updated_at_unix_nanos: now.into(),
            reason: String::new(),
            dependency_watermarks: self
                .preflight
                .as_ref()
                .map(|preflight| preflight.dependency_watermarks())
                .unwrap_or_default(),
            pending_orders: planned_orders.clone(),
            pending_order_due_unix_nanos: scheduled_order_due(&intent, &planned_orders, now),
            quote_version: 0,
            last_quote_refresh_unix_nanos: None,
            compensation_attempts: 0,
        };
        self.intents
            .insert(intent.intent_id.to_string(), state.clone());
        self.commit_intent(IntentEvent {
            intent_id: intent.intent_id.clone(),
            event_sequence: 0.into(),
            status: IntentStatus::Accepted,
            order_ids: Vec::new(),
            completed_quantity: completed_quantity(&intent, 0),
            occurred_at_unix_nanos: now.into(),
            reason: String::new(),
            dependency_watermarks: state.dependency_watermarks.clone(),
        })?;
        self.advance_due_intent_orders(business_now, usize::MAX)?;
        Ok(self
            .intents
            .get(intent.intent_id.as_str())
            .cloned()
            .unwrap_or(state))
    }

    pub fn submit_intent_with_idempotency(
        &mut self,
        intent: ExecuteStrategyIntent,
        idempotency_key: String,
    ) -> Result<(IntentState, bool), ExecutionError> {
        info!(event = "intent_idempotency_check", component = "execution", intent_id = %intent.intent_id, idempotency_key = %idempotency_key, "checking intent idempotency");
        if idempotency_key.trim().is_empty() {
            return Err(ExecutionError::Invalid(
                "idempotency_key is required".into(),
            ));
        }
        if let Some(intent_id) = self.intent_idempotency.get(&idempotency_key) {
            let state = self.intents.get(intent_id).cloned().ok_or_else(|| {
                ExecutionError::Persistence("idempotency record references missing intent".into())
            })?;
            return Ok((state, true));
        }
        let state = self.submit_intent(intent)?;
        self.intent_idempotency
            .insert(idempotency_key, state.intent.intent_id.to_string());
        self.persist_snapshot()?;
        Ok((state, false))
    }

    /// Submit due child orders from durable Intent scheduling state.  The
    /// state loop calls this frequently; command submission therefore never
    /// blocks on a maker cadence or split interval.
    pub fn advance_due_intent_orders(
        &mut self,
        now_unix_nanos: u64,
        limit: usize,
    ) -> Result<usize, ExecutionError> {
        let mut submitted = 0;
        while submitted < limit {
            let next = self
                .intents
                .values()
                .filter_map(|state| {
                    state
                        .pending_orders
                        .iter()
                        .filter_map(|order| {
                            let due = state
                                .pending_order_due_unix_nanos
                                .get(&order.order_id)
                                .copied()
                                .unwrap_or_else(|| now_unix_nanos.into());
                            (due.get() <= now_unix_nanos)
                                .then(|| (due, state.intent.intent_id.clone(), order.clone()))
                        })
                        .min_by_key(|(due, _, _)| *due)
                })
                .min_by_key(|(due, _, _)| *due);
            let Some((_, intent_id, request)) = next else {
                break;
            };
            let leg_id = intent_leg_id(
                &self
                    .intents
                    .get(intent_id.as_str())
                    .ok_or_else(|| ExecutionError::Invalid("scheduled intent disappeared".into()))?
                    .intent,
                &request,
            );
            if let Some(state) = self.intents.get_mut(intent_id.as_str()) {
                state
                    .pending_orders
                    .retain(|order| order.order_id != request.order_id);
                state.pending_order_due_unix_nanos.remove(&request.order_id);
            }
            match self.submit(request.clone()) {
                Ok(order) => {
                    self.attach_plan_order(&intent_id, &leg_id, &order.order_id)?;
                    let state = self
                        .intents
                        .get(intent_id.as_str())
                        .cloned()
                        .ok_or_else(|| {
                            ExecutionError::Invalid("scheduled intent disappeared".into())
                        })?;
                    self.commit_intent(IntentEvent {
                        intent_id: intent_id.clone(),
                        event_sequence: 0.into(),
                        status: IntentStatus::Executing,
                        order_ids: vec![order.order_id.clone()],
                        completed_quantity: state.completed_quantity,
                        occurred_at_unix_nanos: now_unix_nanos.into(),
                        reason: "child order created".into(),
                        dependency_watermarks: state.dependency_watermarks,
                    })?;
                    submitted += 1;
                }
                Err(error) => {
                    warn!(event = "scheduled_child_order_failed", component = "execution", intent_id = %intent_id, error = %error, "scheduled child order failed");
                    let failure_policy = self
                        .intents
                        .get(intent_id.as_str())
                        .map(|state| state.intent.failure_policy)
                        .unwrap_or(FailurePolicy::CancelRemaining);
                    let cancel_siblings = matches!(failure_policy, FailurePolicy::CancelRemaining)
                        || self.intents.get(intent_id.as_str()).is_some_and(|state| {
                            state.intent.intent_type == IntentType::PairArbitrage
                        });
                    if cancel_siblings {
                        let siblings = self
                            .intents
                            .get(intent_id.as_str())
                            .map(|state| state.order_ids.clone())
                            .unwrap_or_default();
                        for order_id in siblings {
                            if self
                                .orders
                                .get(order_id.as_str())
                                .is_some_and(|order| !order.status.terminal())
                            {
                                let _ = self.cancel(CancelOrder {
                                    order_id,
                                    reason: "pair leg submission failed".into(),
                                });
                            }
                        }
                    }
                    let completed_quantity = self
                        .intents
                        .get(intent_id.as_str())
                        .map(|state| state.completed_quantity)
                        .unwrap_or_default();
                    self.commit_intent(IntentEvent {
                        intent_id,
                        event_sequence: 0.into(),
                        status: IntentStatus::Failed,
                        order_ids: Vec::new(),
                        completed_quantity,
                        occurred_at_unix_nanos: now_unix_nanos.into(),
                        reason: error.to_string(),
                        dependency_watermarks: self.dependency_watermarks(),
                    })?;
                    return Err(error);
                }
            }
        }
        if submitted > 0 {
            self.persist_snapshot()?;
        }
        Ok(submitted)
    }

    pub fn cancel_intent(&mut self, request: CancelIntent) -> Result<IntentState, ExecutionError> {
        let state = self
            .intents
            .get(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown intent".into()))?;
        if matches!(
            state.status,
            IntentStatus::Satisfied
                | IntentStatus::Canceled
                | IntentStatus::Rejected
                | IntentStatus::Expired
                | IntentStatus::Failed
                | IntentStatus::ReconciliationRequired
        ) {
            return Err(ExecutionError::Invalid("intent is terminal".into()));
        }
        if let Some(current) = self.intents.get_mut(request.intent_id.as_str()) {
            current.pending_orders.clear();
            current.pending_order_due_unix_nanos.clear();
        }
        self.commit_intent(IntentEvent {
            intent_id: request.intent_id.clone(),
            event_sequence: 0.into(),
            status: IntentStatus::CancelRequested,
            order_ids: state.order_ids.clone(),
            completed_quantity: state.completed_quantity,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: if request.reason.trim().is_empty() {
                "intent cancellation requested".into()
            } else {
                request.reason.clone()
            },
            dependency_watermarks: state.dependency_watermarks.clone(),
        })?;
        for order_id in state.order_ids {
            let active = self
                .orders
                .get(order_id.as_str())
                .is_some_and(|order| !order.status.terminal());
            if active {
                self.cancel(CancelOrder {
                    order_id,
                    reason: request.reason.clone(),
                })?;
            }
        }
        let has_active = self
            .intents
            .get(request.intent_id.as_str())
            .map(|value| {
                value.order_ids.iter().any(|order_id| {
                    self.orders
                        .get(order_id.as_str())
                        .is_some_and(|order| !order.status.terminal())
                })
            })
            .unwrap_or(false);
        if has_active {
            self.refresh_intent(request.intent_id.as_str())?;
        } else {
            let current = self
                .intents
                .get(request.intent_id.as_str())
                .cloned()
                .ok_or_else(|| {
                    ExecutionError::Invalid("intent disappeared during cancellation".into())
                })?;
            self.commit_intent(IntentEvent {
                intent_id: request.intent_id.clone(),
                event_sequence: 0.into(),
                status: IntentStatus::Canceled,
                order_ids: Vec::new(),
                completed_quantity: current.completed_quantity,
                occurred_at_unix_nanos: now_nanos().into(),
                reason: "all pending and active child orders canceled".into(),
                dependency_watermarks: current.dependency_watermarks,
            })?;
        }
        self.intents
            .get(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("intent disappeared during cancellation".into()))
    }

    pub fn expire_intent(&mut self, request: ExpireIntent) -> Result<IntentState, ExecutionError> {
        let state = self
            .intents
            .get(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown intent".into()))?;
        if matches!(
            state.status,
            IntentStatus::Satisfied
                | IntentStatus::Canceled
                | IntentStatus::Rejected
                | IntentStatus::Expired
                | IntentStatus::Failed
                | IntentStatus::ReconciliationRequired
        ) {
            return Err(ExecutionError::Invalid("intent is terminal".into()));
        }
        if let Some(current) = self.intents.get_mut(request.intent_id.as_str()) {
            current.pending_orders.clear();
            current.pending_order_due_unix_nanos.clear();
        }
        self.commit_intent(IntentEvent {
            intent_id: request.intent_id.clone(),
            event_sequence: 0.into(),
            status: IntentStatus::Expired,
            order_ids: state.order_ids.clone(),
            completed_quantity: state.completed_quantity,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: if request.reason.trim().is_empty() {
                "intent expired".into()
            } else {
                request.reason
            },
            dependency_watermarks: state.dependency_watermarks,
        })?;
        for order_id in state.order_ids {
            if self
                .orders
                .get(order_id.as_str())
                .is_some_and(|order| !order.status.terminal())
            {
                let _ = self.cancel(CancelOrder {
                    order_id,
                    reason: "parent intent expired".into(),
                });
            }
        }
        // Child cancellation refreshes the parent intent. Re-assert the
        // deadline outcome after those child lifecycle events so expiration
        // remains the authoritative terminal reason.
        self.commit_intent(IntentEvent {
            intent_id: request.intent_id.clone(),
            event_sequence: 0.into(),
            status: IntentStatus::Expired,
            order_ids: Vec::new(),
            completed_quantity: self
                .intents
                .get(request.intent_id.as_str())
                .map(|value| value.completed_quantity)
                .unwrap_or_default(),
            occurred_at_unix_nanos: now_nanos().into(),
            reason: "intent expired after child cancellation".into(),
            dependency_watermarks: self
                .intents
                .get(request.intent_id.as_str())
                .map(|value| value.dependency_watermarks.clone())
                .unwrap_or_default(),
        })?;
        self.intents
            .get(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("intent disappeared during expiration".into()))
    }

    pub fn expire_due_intents(&mut self, now_unix_nanos: u64) -> Result<usize, ExecutionError> {
        let due = self
            .intents
            .values()
            .filter(|state| {
                !matches!(
                    state.status,
                    IntentStatus::Satisfied
                        | IntentStatus::Canceled
                        | IntentStatus::Rejected
                        | IntentStatus::Expired
                        | IntentStatus::Failed
                        | IntentStatus::ReconciliationRequired
                ) && state
                    .intent
                    .deadline_unix_nanos
                    .is_some_and(|deadline| deadline <= now_unix_nanos.into())
            })
            .map(|state| state.intent.intent_id.clone())
            .collect::<Vec<_>>();
        let count = due.len();
        for intent_id in due {
            self.expire_intent(ExpireIntent {
                intent_id,
                reason: "intent deadline reached".into(),
            })?;
        }
        Ok(count)
    }

    fn commit_intent(&mut self, mut event: IntentEvent) -> Result<(), ExecutionError> {
        let Some(state) = self.intents.get_mut(event.intent_id.as_str()) else {
            return Err(ExecutionError::Invalid(
                "intent event references unknown intent".into(),
            ));
        };
        state.status = event.status;
        state.order_ids.extend(event.order_ids.iter().cloned());
        state.order_ids.sort();
        state.order_ids.dedup();
        state.completed_quantity = event.completed_quantity;
        state.updated_at_unix_nanos = event.occurred_at_unix_nanos;
        state.reason = event.reason.clone();
        self.event_sequence += 1;
        event.event_sequence = self.event_sequence.into();
        self.intent_events.push(event.clone());
        self.pending_intent_events.push(event);
        self.generation += 1;
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            store
                .commit_intent_event(&self.intent_events[self.intent_events.len() - 1], &snapshot)
                .map_err(ExecutionError::Persistence)?;
        }
        Ok(())
    }

    fn persist_snapshot(&mut self) -> Result<(), ExecutionError> {
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            store.save(&snapshot).map_err(ExecutionError::Persistence)?;
        }
        Ok(())
    }

    fn refresh_intent(&mut self, intent_id: &str) -> Result<(), ExecutionError> {
        let Some(state) = self.intents.get(intent_id).cloned() else {
            return Ok(());
        };
        let orders: Vec<ExecutionOrder> = state
            .order_ids
            .iter()
            .filter_map(|order_id| self.orders.get(order_id.as_str()).cloned())
            .collect();
        if orders.is_empty() {
            return Ok(());
        }
        let completed = orders
            .iter()
            .try_fold(Quantity::ZERO, |total, order| {
                total.checked_add(order.filled_quantity)
            })
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
        self.refresh_plan_progress(intent_id, &orders)?;
        let has_pending = self
            .intents
            .get(intent_id)
            .is_some_and(|value| !value.pending_orders.is_empty());
        let all_filled = orders
            .iter()
            .all(|order| order.status == ExecutionOrderStatus::Filled);
        let all_canceled = orders
            .iter()
            .all(|order| order.status == ExecutionOrderStatus::Canceled);
        let has_active = orders.iter().any(|order| !order.status.terminal());
        let has_failed = orders.iter().any(|order| {
            matches!(
                order.status,
                ExecutionOrderStatus::Rejected
                    | ExecutionOrderStatus::Expired
                    | ExecutionOrderStatus::Unknown
                    | ExecutionOrderStatus::Failed
            )
        });
        let target_reached = state.intent.completion_policy
            == CompletionPolicy::TargetQuantityReached
            && state.intent.target_quantity > Quantity::ZERO
            && completed >= state.intent.target_quantity;
        let hedge_within_tolerance =
            if state.intent.completion_policy == CompletionPolicy::HedgeWithinTolerance {
                self.hedge_requirement(intent_id)
                    .ok()
                    .flatten()
                    .is_some_and(|requirement| requirement.within_tolerance)
            } else {
                false
            };
        let best_effort_complete = state.intent.completion_policy == CompletionPolicy::BestEffort
            && !has_active
            && completed > Quantity::ZERO;
        let policy_satisfied =
            !has_active && (target_reached || hedge_within_tolerance || best_effort_complete);
        let status = if (all_filled || policy_satisfied) && !has_pending && !has_failed {
            IntentStatus::Satisfied
        } else if all_canceled && !has_pending {
            IntentStatus::Canceled
        } else if has_pending {
            if completed > Quantity::ZERO {
                IntentStatus::PartiallyFilled
            } else {
                IntentStatus::Executing
            }
        } else if let Some(plan) = self
            .intents
            .get(intent_id)
            .and_then(|state| state.plan.as_ref())
        {
            match plan.lifecycle(orders.iter().map(|order| order.status)) {
                crate::domain::IntentLifecycle::Satisfied => IntentStatus::Satisfied,
                crate::domain::IntentLifecycle::PartiallyFilled => IntentStatus::PartiallyFilled,
                crate::domain::IntentLifecycle::ReconciliationRequired => {
                    IntentStatus::ReconciliationRequired
                }
                crate::domain::IntentLifecycle::Failed => IntentStatus::Failed,
                crate::domain::IntentLifecycle::Canceled => IntentStatus::Canceled,
                _ if has_active => {
                    if completed > Quantity::ZERO {
                        IntentStatus::PartiallyFilled
                    } else {
                        IntentStatus::Executing
                    }
                }
                _ => IntentStatus::Executing,
            }
        } else if has_active {
            if orders
                .iter()
                .any(|order| order.status == ExecutionOrderStatus::Unknown)
            {
                IntentStatus::ReconciliationRequired
            } else if completed > Quantity::ZERO {
                IntentStatus::PartiallyFilled
            } else {
                IntentStatus::Executing
            }
        } else if has_failed {
            if completed > Quantity::ZERO {
                IntentStatus::PartiallyFilled
            } else {
                IntentStatus::Failed
            }
        } else {
            IntentStatus::Executing
        };
        if state.status == status && state.completed_quantity == completed {
            return Ok(());
        }
        self.commit_intent(IntentEvent {
            intent_id: typed_intent_id(intent_id),
            event_sequence: 0.into(),
            status,
            order_ids: state.order_ids,
            completed_quantity: completed,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: match status {
                IntentStatus::Satisfied => "all child orders filled".into(),
                IntentStatus::PartiallyFilled => "child orders partially filled".into(),
                IntentStatus::Canceled => "all child orders canceled".into(),
                IntentStatus::Failed => "all child orders failed".into(),
                IntentStatus::ReconciliationRequired => {
                    "child order reconciliation required".into()
                }
                _ => "child execution progressing".into(),
            },
            dependency_watermarks: state.dependency_watermarks,
        })
    }

    fn attach_plan_order(
        &mut self,
        intent_id: &str,
        leg_id: &str,
        order_id: &str,
    ) -> Result<(), ExecutionError> {
        let state = self
            .intents
            .get_mut(intent_id)
            .ok_or_else(|| ExecutionError::Invalid("intent plan owner is missing".into()))?;
        let plan = state
            .plan
            .as_mut()
            .ok_or_else(|| ExecutionError::Invalid("intent has no execution plan".into()))?;
        let plan_id = plan.plan_id.clone();
        let leg = plan
            .legs
            .iter_mut()
            .find(|leg| leg.leg_id == leg_id)
            .ok_or_else(|| ExecutionError::Invalid("intent plan leg is missing".into()))?;
        let leg_id = leg.leg_id.clone();
        if !leg.order_ids.iter().any(|value| value == order_id) {
            leg.order_ids.push(
                crate::domain::OrderId::new(order_id.to_owned())
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
            );
        }
        if leg.lifecycle == LegLifecycle::Pending {
            leg.transition(LegLifecycle::Ready, "child order prepared")
                .map_err(ExecutionError::Invalid)?;
        }
        if leg.lifecycle == LegLifecycle::Ready {
            leg.transition(LegLifecycle::Executing, "child order submitted")
                .map_err(ExecutionError::Invalid)?;
        }
        if let Some(order) = self.orders.get_mut(order_id) {
            order.plan_id = Some(plan_id);
            order.leg_id = Some(leg_id);
        }
        Ok(())
    }

    fn refresh_plan_progress(
        &mut self,
        intent_id: &str,
        orders: &[ExecutionOrder],
    ) -> Result<(), ExecutionError> {
        let Some(state) = self.intents.get_mut(intent_id) else {
            return Ok(());
        };
        let Some(plan) = state.plan.as_mut() else {
            return Ok(());
        };
        for leg in &mut plan.legs {
            let leg_orders: Vec<_> = orders
                .iter()
                .filter(|order| leg.order_ids.iter().any(|id| id == &order.order_id))
                .collect();
            leg.completed_quantity = leg_orders
                .iter()
                .try_fold(Quantity::ZERO, |total, order| {
                    total.checked_add(order.filled_quantity)
                })
                .map_err(|_| {
                    ExecutionError::Invalid("execution leg completed quantity overflow".into())
                })?;
            let next = if leg_orders.is_empty() {
                leg.lifecycle
            } else if leg_orders
                .iter()
                .any(|order| order.status == ExecutionOrderStatus::Failed)
            {
                LegLifecycle::Failed
            } else if leg_orders
                .iter()
                .all(|order| order.status == ExecutionOrderStatus::Filled)
            {
                LegLifecycle::Satisfied
            } else if leg_orders.iter().any(|order| {
                matches!(
                    order.status,
                    ExecutionOrderStatus::PartiallyFilled | ExecutionOrderStatus::Filled
                )
            }) {
                LegLifecycle::PartiallyFilled
            } else if leg_orders.iter().any(|order| !order.status.terminal()) {
                LegLifecycle::Executing
            } else {
                leg.lifecycle
            };
            leg.transition(next, "child order progress updated")
                .map_err(ExecutionError::Invalid)?;
        }
        Ok(())
    }

    pub fn configure_live_trading(&mut self, enabled: bool, confirmed: bool) {
        self.live_trading = enabled;
        self.live_confirmed = confirmed;
    }

    pub fn attach_preflight(&mut self, preflight: Box<dyn super::ExecutionPreflight>) {
        self.preflight = Some(preflight);
    }

    pub fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.preflight
            .as_ref()
            .map(|preflight| preflight.dependency_watermarks())
            .unwrap_or_default()
    }

    pub fn pending_outbox(
        &mut self,
        limit: u32,
    ) -> Result<Vec<ExecutionOutboxEntry>, ExecutionError> {
        self.store
            .as_mut()
            .map(|store| store.pending_outbox(limit))
            .transpose()
            .map_err(ExecutionError::Persistence)
            .map(|value| value.unwrap_or_default())
    }

    pub fn acknowledge_outbox(&mut self, ids: &[u64]) -> Result<(), ExecutionError> {
        if let Some(store) = self.store.as_mut() {
            store
                .acknowledge_outbox(ids)
                .map_err(ExecutionError::Persistence)?;
        }
        Ok(())
    }

    pub fn latest_checkpoint_unix_nanos(&mut self) -> Result<Option<u64>, ExecutionError> {
        self.store
            .as_mut()
            .map(|store| store.latest_checkpoint_unix_nanos())
            .transpose()
            .map_err(ExecutionError::Persistence)
            .map(|value| value.flatten())
    }

    pub fn preview_submit(&self, request: &SubmitOrder) -> Result<ExecutionOrder, ExecutionError> {
        let mut order = ExecutionOrder::new(
            request.order_id.to_string(),
            request.account_id.to_string(),
            request.segment_key.to_string(),
            request.instrument_id.to_string(),
            request.side,
            request.order_type,
            request.quantity,
            now_nanos(),
        )
        .map_err(ExecutionError::Invalid)?;
        order.intent_id = request.intent_id.clone();
        order.market_id = request.market_id.clone();
        order.limit_price = request.limit_price;
        order.reason = "dry-run preview".into();
        Ok(order)
    }

    pub fn orders(&self, account_id: Option<&str>) -> Vec<ExecutionOrder> {
        self.orders
            .values()
            .filter(|order| account_id.is_none_or(|id| order.account_id == id))
            .cloned()
            .collect()
    }

    pub fn events(&self, order_id: Option<&str>) -> Vec<ExecutionEvent> {
        self.events
            .iter()
            .filter(|event| order_id.is_none_or(|id| event.order_id.as_str() == id))
            .cloned()
            .collect()
    }

    pub fn trace(&self, order_id: &str) -> Vec<ExecutionEvent> {
        self.events(Some(order_id))
    }

    pub fn audit_events(
        &mut self,
        query: ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, ExecutionError> {
        let mut events = self
            .events
            .iter()
            .enumerate()
            .map(|(index, event)| ExecutionAuditEvent {
                sequence: (index as u64 + 1).into(),
                order_id: event.order_id.clone(),
                status: event.status,
                remote_order_id: event.remote_order_id.as_ref().cloned(),
                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                reason: event.reason.clone(),
            })
            .filter(|event| audit_matches(event, &query))
            .collect::<Vec<_>>();
        if let Some(limit) = query.limit {
            events.truncate(limit as usize);
        }
        Ok(events)
    }

    pub fn fills(&self, order_id: Option<&str>) -> Vec<ExecutionFill> {
        self.fills
            .iter()
            .filter(|fill| order_id.is_none_or(|id| fill.order_id == id))
            .cloned()
            .collect()
    }

    pub fn record_fill(
        &mut self,
        request: ExecutionFillReport,
    ) -> Result<ExecutionOrder, ExecutionError> {
        info!(event = "fill_received", component = "execution", fill_id = %request.fill_id, order_id = %request.order_id, "execution fill received");
        if let Some(existing) = self
            .fills
            .iter()
            .find(|fill| fill.fill_id.as_str() == request.fill_id.as_str())
            .cloned()
        {
            let same_fact = existing.order_id.as_str() == request.order_id.as_str()
                && existing.quantity == request.quantity
                && existing.price == request.price
                && existing.fee == request.fee
                && request
                    .occurred_at_unix_nanos
                    .is_none_or(|value| value.get() == existing.occurred_at_unix_nanos.get());
            if same_fact {
                return self
                    .orders
                    .get(request.order_id.as_str())
                    .cloned()
                    .ok_or_else(|| {
                        ExecutionError::Invalid("duplicate fill references unknown order".into())
                    });
            }
            if let Some(intent_id) = existing.intent_id.as_deref() {
                if let Some(state) = self.intents.get(intent_id).cloned() {
                    self.commit_intent(IntentEvent {
                        intent_id: typed_intent_id(intent_id),
                        event_sequence: 0.into(),
                        status: IntentStatus::ReconciliationRequired,
                        order_ids: Vec::new(),
                        completed_quantity: state.completed_quantity,
                        occurred_at_unix_nanos: now_nanos().into(),
                        reason: format!("conflicting duplicate fill: {}", request.fill_id),
                        dependency_watermarks: state.dependency_watermarks,
                    })?;
                }
            }
            return Err(ExecutionError::Invalid("conflicting duplicate fill".into()));
        }
        if request.quantity.mantissa() <= 0 || request.price.mantissa() <= 0 {
            return Err(ExecutionError::Invalid(
                "fill quantity and price must be positive".into(),
            ));
        }
        if request.fee.mantissa() < 0 {
            return Err(ExecutionError::Invalid(
                "fill fee cannot be negative".into(),
            ));
        }
        let current = self
            .orders
            .get(request.order_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown order".into()))?;
        if current.status.terminal() {
            return Err(ExecutionError::Invalid("order is terminal".into()));
        }
        let filled = current
            .filled_quantity
            .checked_add(request.quantity)
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
        if filled > current.quantity {
            return Err(ExecutionError::Invalid(
                "cumulative fill exceeds order quantity".into(),
            ));
        }
        let now = request
            .occurred_at_unix_nanos
            .unwrap_or_else(|| now_nanos().into());
        let mut next = current.clone();
        next.filled_quantity = filled;
        next.updated_at_unix_nanos = crate::domain::UnixNanos::new(now.get());
        next.status = if filled == next.quantity {
            ExecutionOrderStatus::Filled
        } else {
            ExecutionOrderStatus::PartiallyFilled
        };
        let fill = ExecutionFill {
            fill_id: crate::domain::FillId::new(request.fill_id.to_string())
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
            order_id: next.order_id.clone(),
            plan_id: next.plan_id.clone(),
            leg_id: next.leg_id.clone(),
            intent_id: next.intent_id.clone(),
            instrument_id: next.instrument_id.clone(),
            side: next.side,
            quantity: crate::domain::Quantity::new(
                request.quantity.mantissa(),
                request.quantity.scale(),
            )
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
            price: crate::domain::Price::new(request.price.mantissa(), request.price.scale())
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
            fee: crate::domain::Money::new(request.fee.mantissa(), request.fee.scale())
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
            occurred_at_unix_nanos: crate::domain::UnixNanos::new(now.get()),
        };
        self.orders.insert(next.order_id.clone(), next.clone());
        self.fills.push(fill.clone());
        self.commit(ExecutionEvent {
            order_id: next.order_id.clone(),
            intent_id: next.intent_id.clone(),
            plan_id: next.plan_id.clone(),
            leg_id: next.leg_id.clone(),
            status: next.status,
            remote_order_id: next.remote_order_id.clone(),
            occurred_at_unix_nanos: now,
            reason: String::new(),
            fill_id: Some(request.fill_id.clone()),
            filled_quantity: Some(filled),
        })?;
        if let Some(preflight) = self.preflight.as_mut() {
            preflight
                .publish_fill(&fill)
                .map_err(ExecutionError::Invalid)?;
            preflight
                .publish_order(&next)
                .map_err(ExecutionError::Invalid)?;
        }
        if let Some(preflight) = self.preflight.as_mut() {
            if next.status == ExecutionOrderStatus::Filled {
                preflight
                    .consume_order(&next.order_id)
                    .map_err(ExecutionError::Invalid)?;
            } else if next.status == ExecutionOrderStatus::PartiallyFilled {
                let remaining = next
                    .quantity
                    .checked_sub(next.filled_quantity)
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
                preflight
                    .resize_order(&next.order_id, remaining)
                    .map_err(ExecutionError::Invalid)?;
            }
        }
        if let Some(intent_id) = next.intent_id.as_deref() {
            self.refresh_intent(intent_id)?;
            self.maybe_submit_compensating_hedge(intent_id)?;
        }
        info!(event = "fill_applied", component = "execution", fill_id = %fill.fill_id, order_id = %next.order_id, status = ?next.status, filled_quantity = next.filled_quantity.mantissa(), "execution fill applied");
        Ok(next)
    }

    pub fn prepare_submission(
        &mut self,
        request: SubmitOrder,
    ) -> Result<(ExecutionOrder, OrderEntryRequest), ExecutionError> {
        info!(event = "order_submission_preparing", component = "execution", order_id = %request.order_id, intent_id = ?request.intent_id, account_id = %request.account_id, instrument_id = %request.instrument_id, live_trading = self.live_trading, "order submission preparing");
        if self.live_trading && !self.live_confirmed {
            return Err(ExecutionError::Invalid(
                "live order submission requires explicit confirmation".into(),
            ));
        }
        let now = request
            .submitted_at_unix_nanos
            .map(UnixNanos::get)
            .unwrap_or_else(now_nanos);
        if self.orders.contains_key(request.order_id.as_str()) {
            return Err(ExecutionError::Invalid("order_id already exists".into()));
        }
        if let Some(preflight) = self.preflight.as_mut() {
            preflight
                .validate_order(&request)
                .map_err(ExecutionError::Invalid)?;
            preflight
                .reserve_order(&request)
                .map_err(ExecutionError::Invalid)?;
            if let Err(error) = preflight.prepare_order(&request) {
                let _ = preflight.release_order(&request.order_id);
                return Err(ExecutionError::Invalid(error));
            }
        }
        let options = request.options.clone();
        let mut order = ExecutionOrder::new(
            request.order_id.to_string(),
            request.account_id.to_string(),
            request.segment_key.to_string(),
            request.instrument_id.to_string(),
            request.side,
            request.order_type,
            request.quantity,
            now,
        )
        .map_err(ExecutionError::Invalid)?;
        order.intent_id = request.intent_id.clone();
        order.market_id = request.market_id.clone();
        order.limit_price = request.limit_price;
        order.status = ExecutionOrderStatus::Submitting;
        let connection_request = to_connection_request(&order, &request.segment_key, &options)
            .map_err(ExecutionError::Invalid)?;
        self.orders.insert(order.order_id.clone(), order.clone());
        self.commit(ExecutionEvent {
            order_id: order.order_id.clone(),
            intent_id: order.intent_id.clone(),
            plan_id: order.plan_id.clone(),
            leg_id: order.leg_id.clone(),
            status: order.status,
            remote_order_id: None,
            occurred_at_unix_nanos: now.into(),
            reason: String::new(),
            fill_id: None,
            filled_quantity: None,
        })?;
        Ok((order, connection_request))
    }

    /// Apply a provider response or private-stream update to one local order.
    /// The gateway worker calls this through the exchange event mailbox; it does
    /// not call the provider from inside this method.
    pub fn apply_order_entry_event(
        &mut self,
        order_id: &str,
        event: OrderEntryEvent,
    ) -> Result<ExecutionOrder, ExecutionError> {
        let current = self
            .orders
            .get(order_id)
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown order".into()))?;
        let mut order = current;
        let occurred_at = event.occurred_at_unix_nanos;
        apply_connection_event(&mut order, event)?;
        if order.status == ExecutionOrderStatus::Accepted && order.remote_order_id.is_none() {
            order.status = ExecutionOrderStatus::Unknown;
            order.reason = "accepted order did not return a exchange order id".into();
        }
        if let Some(preflight) = self.preflight.as_mut() {
            match order.status {
                ExecutionOrderStatus::Rejected
                | ExecutionOrderStatus::Canceled
                | ExecutionOrderStatus::Expired
                | ExecutionOrderStatus::Failed
                | ExecutionOrderStatus::Unknown => {
                    let _ = preflight.release_order(&order.order_id);
                }
                ExecutionOrderStatus::Filled => {
                    let _ = preflight.consume_order(&order.order_id);
                }
                _ => {}
            }
        }
        self.orders.insert(order.order_id.clone(), order.clone());
        self.commit(ExecutionEvent {
            order_id: order.order_id.clone(),
            intent_id: order.intent_id.clone(),
            plan_id: order.plan_id.clone(),
            leg_id: order.leg_id.clone(),
            status: order.status,
            remote_order_id: order.remote_order_id.clone(),
            occurred_at_unix_nanos: occurred_at,
            reason: String::new(),
            fill_id: None,
            filled_quantity: None,
        })?;
        if let Some(preflight) = self.preflight.as_mut() {
            preflight
                .publish_order(&order)
                .map_err(ExecutionError::Invalid)?;
        }
        if let Some(intent_id) = order
            .intent_id
            .as_deref()
            .filter(|id| self.intents.contains_key(*id))
        {
            self.refresh_intent(intent_id)?;
        }
        info!(event = "order_submitted", component = "execution", order_id = %order.order_id, remote_order_id = ?order.remote_order_id, status = ?order.status, "order submission completed");
        Ok(order)
    }

    fn mark_not_sent(
        &mut self,
        order_id: &str,
        reason: impl Into<String>,
    ) -> Result<(), ExecutionError> {
        let reason = reason.into();
        if let Some(preflight) = self.preflight.as_mut() {
            let _ = preflight.release_order(order_id);
        }
        let Some(mut order) = self.orders.get(order_id).cloned() else {
            return Ok(());
        };
        let now = now_nanos();
        order.status = ExecutionOrderStatus::Failed;
        order.reason = reason.clone();
        order.updated_at_unix_nanos = crate::domain::UnixNanos::new(now);
        self.orders.insert(order.order_id.clone(), order.clone());
        self.commit(ExecutionEvent {
            order_id: order.order_id.clone(),
            intent_id: order.intent_id.clone(),
            plan_id: order.plan_id.clone(),
            leg_id: order.leg_id.clone(),
            status: order.status,
            remote_order_id: order.remote_order_id.clone(),
            occurred_at_unix_nanos: now.into(),
            reason,
            fill_id: None,
            filled_quantity: None,
        })?;
        if let Some(intent_id) = order.intent_id.as_deref() {
            self.refresh_intent(intent_id)?;
        }
        Ok(())
    }

    pub fn submit(&mut self, request: SubmitOrder) -> Result<ExecutionOrder, ExecutionError> {
        let (order, connection_request) = self.prepare_submission(request)?;
        let Some(connection) = self.order_entry.as_mut() else {
            self.mark_not_sent(&order.order_id, "order entry connection is not configured")?;
            return Err(ExecutionError::Gateway(
                "order entry connection is not configured".into(),
            ));
        };
        let event = match connection.submit_order(&connection_request) {
            Ok(CommandOutcome::Confirmed(event)) => event,
            Ok(CommandOutcome::Rejected(rejection)) => OrderEntryEvent {
                order_id: order.order_id.clone(),
                status: OrderEntryStatus::Rejected,
                remote_order_id: None,
                filled_quantity: None,
                occurred_at_unix_nanos: now_nanos().into(),
                reason: rejection.message,
            },
            Ok(CommandOutcome::Indeterminate(command)) => {
                warn!(event = "order_submission_indeterminate", component = "execution", order_id = %order.order_id, error = %command.message, "provider order submission requires reconciliation");
                self.mark_unknown_after_gateway_error(&order.order_id, command.message.clone())?;
                return Err(ExecutionError::Indeterminate(command.message));
            }
            Err(error) => {
                warn!(event = "order_submission_failed", component = "execution", order_id = %order.order_id, error = %error, "provider order submission failed");
                let message = error.to_string();
                // The Integration command contract reserves an ordinary Err
                // for failures proven to occur before delivery. Any failure
                // after command dispatch must be returned as Indeterminate.
                self.mark_not_sent(&order.order_id, message.clone())?;
                return Err(ExecutionError::Gateway(message));
            }
        };
        self.apply_order_entry_event(&order.order_id, event)
    }

    fn mark_unknown_after_gateway_error(
        &mut self,
        order_id: &str,
        reason: String,
    ) -> Result<(), ExecutionError> {
        let Some(mut order) = self.orders.get(order_id).cloned() else {
            return Ok(());
        };
        let now = now_nanos();
        order.status = ExecutionOrderStatus::Unknown;
        order.reason = reason.clone();
        order.updated_at_unix_nanos = crate::domain::UnixNanos::new(now);
        self.orders.insert(order.order_id.clone(), order.clone());
        self.commit(ExecutionEvent {
            order_id: OrderId::new(order_id).expect("validated order ID"),
            intent_id: order.intent_id.clone(),
            plan_id: order.plan_id.clone(),
            leg_id: order.leg_id.clone(),
            status: order.status,
            remote_order_id: order.remote_order_id.clone(),
            occurred_at_unix_nanos: now.into(),
            reason,
            fill_id: None,
            filled_quantity: None,
        })?;
        if let Some(intent_id) = order.intent_id.as_deref() {
            self.refresh_intent(intent_id)?;
        }
        Ok(())
    }

    pub fn cancel(&mut self, request: CancelOrder) -> Result<ExecutionOrder, ExecutionError> {
        info!(event = "order_cancel_started", component = "execution", order_id = %request.order_id, reason = %request.reason, "order cancellation started");
        let order = self
            .orders
            .get(request.order_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown order".into()))?;
        if order.status.terminal() {
            return Err(ExecutionError::Invalid("order is terminal".into()));
        }
        let connection_request = to_connection_request(
            &order,
            &order.segment_key,
            &ExecutionOrderOptions::default(),
        )
        .map_err(ExecutionError::Invalid)?;
        let outcome = self
            .order_entry
            .as_mut()
            .ok_or_else(|| {
                ExecutionError::Gateway("order entry connection is not configured".into())
            })?
            .cancel_order(
                &connection_request,
                order.remote_order_id.as_deref().unwrap_or_default(),
                now_nanos(),
            );
        let event = match outcome {
            Ok(CommandOutcome::Confirmed(event)) => event,
            Ok(CommandOutcome::Rejected(rejection)) => {
                warn!(event = "order_cancel_rejected", component = "execution", order_id = %order.order_id, error = %rejection.message, "provider rejected order cancellation");
                return Err(ExecutionError::ProviderRejected(rejection.message));
            }
            Ok(CommandOutcome::Indeterminate(command)) => {
                warn!(event = "order_cancel_indeterminate", component = "execution", order_id = %order.order_id, error = %command.message, "provider order cancellation requires reconciliation");
                self.mark_unknown_after_gateway_error(&order.order_id, command.message.clone())?;
                return Err(ExecutionError::Indeterminate(command.message));
            }
            Err(error) => {
                warn!(event = "order_cancel_failed", component = "execution", order_id = %order.order_id, error = %error, "provider order cancellation failed");
                // No cancel command reached the provider. The original order
                // remains in its current state and does not require recovery
                // solely because a local/pre-delivery cancel attempt failed.
                return Err(ExecutionError::Gateway(error.to_string()));
            }
        };
        let now = now_nanos();
        let mut next = order;
        apply_connection_event(&mut next, event)?;
        next.updated_at_unix_nanos = crate::domain::UnixNanos::new(now);
        next.reason = request.reason.clone();
        self.orders.insert(next.order_id.clone(), next.clone());
        self.commit(ExecutionEvent {
            order_id: next.order_id.clone(),
            intent_id: next.intent_id.clone(),
            plan_id: next.plan_id.clone(),
            leg_id: next.leg_id.clone(),
            status: next.status,
            remote_order_id: next.remote_order_id.clone(),
            occurred_at_unix_nanos: now.into(),
            reason: request.reason,
            fill_id: None,
            filled_quantity: None,
        })?;
        if let Some(preflight) = self.preflight.as_mut() {
            preflight
                .publish_order(&next)
                .map_err(ExecutionError::Invalid)?;
        }
        if matches!(
            next.status,
            ExecutionOrderStatus::Canceled
                | ExecutionOrderStatus::Rejected
                | ExecutionOrderStatus::Expired
                | ExecutionOrderStatus::Failed
        ) {
            if let Some(preflight) = self.preflight.as_mut() {
                preflight
                    .release_order(&next.order_id)
                    .map_err(ExecutionError::Invalid)?;
            }
        }
        if let Some(intent_id) = next.intent_id.as_deref() {
            self.refresh_intent(intent_id)?;
        }
        info!(event = "order_cancelled", component = "execution", order_id = %next.order_id, status = ?next.status, "order cancellation completed");
        Ok(next)
    }

    pub fn replace(&mut self, request: ReplaceOrder) -> Result<ExecutionOrder, ExecutionError> {
        info!(event = "order_replace_started", component = "execution", order_id = %request.order_id, replacement_order_id = %request.replacement.order_id, "order replacement started");
        let current = self
            .orders
            .get(request.order_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown order".into()))?;
        if !current.status.terminal() {
            self.cancel(CancelOrder {
                order_id: request.order_id,
                reason: "replaced".into(),
            })?;
        }
        let result = self.submit(request.replacement);
        match &result {
            Ok(order) => {
                info!(event = "order_replaced", component = "execution", order_id = %order.order_id, status = ?order.status, "order replacement completed")
            }
            Err(error) => {
                warn!(event = "order_replace_failed", component = "execution", error = %error, "order replacement failed")
            }
        }
        result
    }

    /// Refresh both sides of a persistent maker quote.  A refresh is a
    /// lifecycle operation, not a second order owner: old orders remain in
    /// the plan as canceled/fill history and the replacement orders are
    /// attached to the same intent and leg.
    pub fn refresh_quote_intent(
        &mut self,
        request: RefreshQuoteIntent,
    ) -> Result<IntentState, ExecutionError> {
        let state = self
            .intents
            .get(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("unknown intent".into()))?;
        if state.intent.intent_type != IntentType::QuoteProvisioning {
            return Err(ExecutionError::Invalid(
                "quote refresh requires a QuoteProvisioning intent".into(),
            ));
        }
        if request.bid_price.mantissa() <= 0 || request.ask_price.mantissa() <= 0 {
            return Err(ExecutionError::Invalid(
                "quote refresh prices must be positive".into(),
            ));
        }
        if request.bid_price >= request.ask_price {
            return Err(ExecutionError::Invalid(
                "quote refresh requires bid below ask".into(),
            ));
        }
        let now = now_nanos();
        if request.quote_observed_at.get() > now {
            return Err(ExecutionError::Invalid(
                "quote observation cannot be in the future".into(),
            ));
        }
        let max_age = state
            .intent
            .legs
            .iter()
            .filter_map(|leg| leg.options.maker.as_ref())
            .chain(state.intent.order_options.maker.as_ref())
            .filter_map(|policy| policy.max_quote_age)
            .min();
        if let Some(max_age) = max_age {
            let age = now.saturating_sub(request.quote_observed_at.get());
            if age > max_age.get() {
                return Err(ExecutionError::Invalid(format!(
                    "quote is stale: age={}ms exceeds {}ms",
                    age / 1_000_000,
                    max_age.get() / 1_000_000
                )));
            }
        }
        if let Some(last) = state.last_quote_refresh_unix_nanos {
            let min_interval = state
                .intent
                .legs
                .iter()
                .filter_map(|leg| leg.options.maker.as_ref())
                .chain(state.intent.order_options.maker.as_ref())
                .filter_map(|policy| policy.min_interval)
                .max()
                .unwrap_or(DurationNanos::new(0));
            if now.saturating_sub(last.get()) < min_interval.get() {
                return Err(ExecutionError::Invalid(
                    "quote refresh violates maker minimum interval".into(),
                ));
            }
        }
        let plan = state
            .plan
            .clone()
            .ok_or_else(|| ExecutionError::Invalid("quote intent has no execution plan".into()))?;
        let version = state.quote_version.saturating_add(1);
        let mut templates = Vec::new();
        for leg in &plan.legs {
            let template = leg
                .order_ids
                .iter()
                .rev()
                .filter_map(|order_id| self.orders.get(order_id.as_str()))
                .find(|order| !order.status.terminal())
                .or_else(|| {
                    leg.order_ids
                        .iter()
                        .rev()
                        .filter_map(|order_id| self.orders.get(order_id.as_str()))
                        .next()
                })
                .cloned()
                .ok_or_else(|| {
                    ExecutionError::Invalid(format!(
                        "quote leg has no order template: {}",
                        leg.leg_id
                    ))
                })?;
            templates.push((leg.leg_id.clone(), template));
        }
        for (_, template) in &templates {
            if !template.status.terminal() {
                self.cancel(CancelOrder {
                    order_id: template.order_id.clone(),
                    reason: "maker quote refresh".into(),
                })?;
            }
        }
        let mut new_order_ids: Vec<OrderId> = Vec::new();
        for (leg_id, template) in templates {
            let options = template_options(&state.intent, &leg_id);
            let mut replacement = SubmitOrder {
                order_id: OrderId::new(format!(
                    "{}:quote:{}:{}",
                    request.intent_id, version, leg_id
                ))
                .expect("validated quote order ID"),
                intent_id: Some(request.intent_id.clone()),
                account_id: template.account_id.clone(),
                segment_key: template.segment_key.clone(),
                instrument_id: template.instrument_id.clone(),
                market_id: template.market_id.clone(),
                side: template.side,
                order_type: OrderType::Limit,
                quantity: Quantity::new(
                    plan.legs
                        .iter()
                        .find(|leg| leg.leg_id == leg_id)
                        .map(|leg| leg.target_quantity.mantissa())
                        .unwrap_or(template.quantity.mantissa()),
                    template.quantity.scale(),
                )
                .expect("validated quote quantity"),
                limit_price: Some(
                    Price::new(
                        if template.side == OrderSide::Buy {
                            request.bid_price.mantissa()
                        } else {
                            request.ask_price.mantissa()
                        },
                        if template.side == OrderSide::Buy {
                            request.bid_price.scale()
                        } else {
                            request.ask_price.scale()
                        },
                    )
                    .expect("validated quote price"),
                ),
                options,
                submitted_at_unix_nanos: Some(request.quote_observed_at),
            };
            replacement.options.post_only = Some(true);
            let order = match self.submit(replacement) {
                Ok(order) => order,
                Err(error) => {
                    let current = self
                        .intents
                        .get(request.intent_id.as_str())
                        .cloned()
                        .ok_or_else(|| {
                            ExecutionError::Invalid("quote intent disappeared".into())
                        })?;
                    self.commit_intent(IntentEvent {
                        intent_id: request.intent_id.clone(),
                        event_sequence: 0.into(),
                        status: IntentStatus::ReconciliationRequired,
                        order_ids: Vec::new(),
                        completed_quantity: current.completed_quantity,
                        occurred_at_unix_nanos: now.into(),
                        reason: format!("maker quote refresh failed: {error}"),
                        dependency_watermarks: current.dependency_watermarks,
                    })?;
                    return Err(error);
                }
            };
            self.attach_plan_order(request.intent_id.as_str(), &leg_id, &order.order_id)?;
            new_order_ids.push(order.order_id);
        }
        if let Some(current) = self.intents.get_mut(request.intent_id.as_str()) {
            current.quote_version = version;
            current.last_quote_refresh_unix_nanos = Some(now.into());
        }
        let current = self
            .intents
            .get(request.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("quote intent disappeared".into()))?;
        self.commit_intent(IntentEvent {
            intent_id: request.intent_id,
            event_sequence: 0.into(),
            status: IntentStatus::Executing,
            order_ids: new_order_ids,
            completed_quantity: current.completed_quantity,
            occurred_at_unix_nanos: now.into(),
            reason: if request.reason.trim().is_empty() {
                "maker quote refreshed".into()
            } else {
                request.reason
            },
            dependency_watermarks: current.dependency_watermarks.clone(),
        })?;
        self.persist_snapshot()?;
        self.intents
            .get(current.intent.intent_id.as_str())
            .cloned()
            .ok_or_else(|| ExecutionError::Invalid("quote intent disappeared".into()))
    }

    /// Pull current projected quotes through the composition preflight and
    /// automatically refresh changed QuoteProvisioning intents.  The market
    /// projection is advisory; every replacement still goes through the
    /// normal order validation, reservation and lifecycle path.
    pub fn refresh_maker_quotes(&mut self) -> Result<usize, ExecutionError> {
        let targets = self
            .intents
            .values()
            .filter(|state| {
                state.intent.intent_type == IntentType::QuoteProvisioning
                    && !matches!(
                        state.status,
                        IntentStatus::Satisfied
                            | IntentStatus::Rejected
                            | IntentStatus::Canceled
                            | IntentStatus::Expired
                            | IntentStatus::Failed
                            | IntentStatus::ReconciliationRequired
                    )
            })
            .map(|state| {
                (
                    state.intent.intent_id.clone(),
                    state.intent.instrument_id.clone(),
                    state.intent.market_id.clone(),
                )
            })
            .collect::<Vec<_>>();
        if targets.is_empty() || self.preflight.is_none() {
            return Ok(0);
        }
        let observations = {
            let preflight = self.preflight.as_mut().expect("preflight checked above");
            targets
                .iter()
                .map(|(intent_id, instrument_id, market_id)| {
                    preflight
                        .latest_quote(instrument_id, market_id.as_deref())
                        .map(|quote| (intent_id.clone(), quote))
                })
                .collect::<Result<Vec<_>, _>>()
                .map_err(ExecutionError::Invalid)?
        };
        let mut refreshed = 0;
        for (intent_id, quote) in observations {
            let Some(quote) = quote else {
                continue;
            };
            let (Some(bid), Some(ask)) = (quote.bid_price, quote.ask_price) else {
                continue;
            };
            let changed = self
                .intents
                .get(intent_id.as_str())
                .and_then(|state| state.plan.as_ref())
                .map(|plan| {
                    let current_price = |side: OrderSide| {
                        plan.legs
                            .iter()
                            .filter(|leg| leg.side == side)
                            .flat_map(|leg| leg.order_ids.iter().rev())
                            .filter_map(|id| self.orders.get(id))
                            .find(|order| !order.status.terminal())
                            .and_then(|order| order.limit_price)
                    };
                    current_price(OrderSide::Buy) != Some(bid)
                        || current_price(OrderSide::Sell) != Some(ask)
                })
                .unwrap_or(false);
            if !changed {
                continue;
            }
            match self.refresh_quote_intent(RefreshQuoteIntent {
                intent_id,
                bid_price: bid,
                ask_price: ask,
                quote_observed_at: quote.observed_at_unix_nanos,
                reason: "projected market quote changed".into(),
            }) {
                Ok(_) => refreshed += 1,
                Err(error) => warn!(
                    event = "maker_quote_refresh_skipped",
                    component = "execution",
                    error = %error,
                    "maker quote refresh was rejected by execution guardrails"
                ),
            }
        }
        Ok(refreshed)
    }

    fn commit(&mut self, event: ExecutionEvent) -> Result<(), ExecutionError> {
        debug!(event = "execution_event_committing", component = "execution", order_id = %event.order_id, status = ?event.status, "execution event committing");
        self.event_sequence += 1;
        self.generation += 1;
        self.events.push(event.clone());
        self.pending_events.push(event);
        let snapshot = self.snapshot();
        if let Some(store) = self.store.as_mut() {
            store
                .commit_event(&self.events[self.events.len() - 1], &snapshot)
                .map_err(ExecutionError::Persistence)?;
        }
        Ok(())
    }

    fn record_unknown_remote_order(
        &mut self,
        event: &RemoteOrderUpdate,
    ) -> Result<(), ExecutionError> {
        let now = event.occurred_at_unix_nanos;
        let remote_order_id = kairos_domain_types::RemoteOrderId::new(event.order_id.to_string())
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
        let entry = self
            .unknown_remote_orders
            .entry(event.order_id.to_string())
            .or_insert_with(|| UnknownRemoteOrder {
                remote_order_id: remote_order_id.clone(),
                symbol: event.symbol.clone(),
                status: event.status,
                execution_id: event.execution_id.clone(),
                fill_quantity: event.fill_quantity,
                fill_price: event.fill_price,
                fee_currency: event.fee_currency.clone(),
                fee_amount: event.fee_amount,
                first_seen_at_unix_nanos: now,
                last_seen_at_unix_nanos: now,
                resolution: UnknownRemoteOrderResolution::Pending,
                reason: event.reason.clone(),
            });
        entry.symbol = event.symbol.clone();
        entry.status = event.status;
        entry.execution_id = event.execution_id.clone();
        entry.fill_quantity = event.fill_quantity;
        entry.fill_price = event.fill_price;
        entry.fee_currency = event.fee_currency.clone();
        entry.fee_amount = event.fee_amount;
        entry.last_seen_at_unix_nanos = now;
        entry.reason = event.reason.clone();
        self.persist_snapshot()
    }
}

fn to_connection_request(
    order: &ExecutionOrder,
    segment_key: &str,
    options: &ExecutionOrderOptions,
) -> Result<OrderEntryRequest, String> {
    let market_id = order.market_id.as_deref();
    // Transitional projection: Phase 3 replaces this legacy MarketId parsing
    // with the selected Execution route's Reference-owned provider mapping.
    // The provider adapter already receives an explicit ProviderInstrumentRef
    // and no longer reverse-engineers canonical identity strings.
    let participant_id = market_id
        .and_then(|value| value.split(':').nth(1))
        .filter(|value| !value.trim().is_empty())
        .unwrap_or("unresolved");
    let source_symbol = market_id
        .and_then(|value| value.rsplit(':').next())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(order.instrument_id.as_str());
    let provider_instrument = kairos_integration::application::ProviderInstrumentRef::new(
        kairos_integration::application::ParticipantRef::new(
            kairos_integration::application::ParticipantKind::Exchange,
            participant_id,
        )?,
        None,
        source_symbol,
    )?;
    Ok(OrderEntryRequest {
        order_id: order.order_id.clone(),
        intent_id: order.intent_id.clone(),
        account_id: order.account_id.clone(),
        segment_key: kairos_domain_types::SegmentKey::new(segment_key)
            .map_err(|error| error.to_string())?,
        instrument_id: order.instrument_id.clone(),
        market_id: order.market_id.clone(),
        provider_instrument,
        side: match order.side {
            OrderSide::Buy => ConnectionOrderSide::Buy,
            OrderSide::Sell => ConnectionOrderSide::Sell,
        },
        quantity: ConnectionDecimalValue::new(order.quantity.mantissa(), order.quantity.scale()),
        order_type: match order.order_type {
            OrderType::Market => ConnectionOrderType::Market,
            OrderType::Limit => ConnectionOrderType::Limit,
        },
        limit_price: order
            .limit_price
            .map(|price| ConnectionDecimalValue::new(price.mantissa(), price.scale())),
        options: ConnectionOrderEntryOptions {
            time_in_force: options
                .time_in_force
                .as_deref()
                .map(parse_time_in_force)
                .transpose()?,
            reduce_only: options.reduce_only,
            post_only: options.post_only,
            position_side: options.position_side.clone(),
            quote_asset: options.quote_asset.clone(),
            wallet_type: options.wallet_type.clone(),
            trading_session: options.trading_session.clone(),
            tokenize: options.tokenize,
        },
    })
}

fn parse_time_in_force(value: &str) -> Result<TimeInForce, String> {
    match value.trim().to_ascii_uppercase().as_str() {
        "GTC" | "GOOD_TIL_CANCELED" => Ok(TimeInForce::GoodTilCanceled),
        "IOC" | "IMMEDIATE_OR_CANCEL" => Ok(TimeInForce::ImmediateOrCancel),
        "FOK" | "FILL_OR_KILL" => Ok(TimeInForce::FillOrKill),
        "DAY" => Ok(TimeInForce::Day),
        _ => Err(format!("unsupported time_in_force: {value}")),
    }
}

fn expand_child_orders(
    intent: &ExecuteStrategyIntent,
    orders: Vec<SubmitOrder>,
) -> Result<Vec<SubmitOrder>, ExecutionError> {
    let mut expanded = Vec::with_capacity(orders.len());
    for order in orders {
        let Some(policy) = order.options.split.as_ref() else {
            expanded.push(order);
            continue;
        };
        let chunks = split_quantity(order.quantity, policy).map_err(|error| {
            ExecutionError::Invalid(format!("split {}: {error}", order.order_id))
        })?;
        if chunks.len() == 1 {
            expanded.push(order);
            continue;
        }
        for (index, quantity) in chunks.into_iter().enumerate() {
            let mut child = order.clone();
            child.order_id = OrderId::new(format!("{}:child:{index}", order.order_id))
                .expect("validated child order ID");
            child.quantity = quantity;
            expanded.push(child);
        }
    }
    if expanded.iter().any(|order| {
        order
            .intent_id
            .as_ref()
            .is_none_or(|id| id.as_str() != intent.intent_id.as_str())
    }) {
        return Err(ExecutionError::Invalid(
            "split planner produced an order outside the intent".into(),
        ));
    }
    Ok(expanded)
}

fn intent_leg_id(intent: &ExecuteStrategyIntent, order: &SubmitOrder) -> String {
    if let Some(leg) = intent.legs.iter().find(|leg| {
        order
            .order_id
            .starts_with(&format!("{}:order:{}", intent.intent_id, leg.leg_id))
    }) {
        return leg.leg_id.to_string();
    }
    let prefix = format!("{}:order:", intent.intent_id);
    let ordinal = order
        .order_id
        .strip_prefix(&prefix)
        .and_then(|value| value.split(":child:").next())
        .unwrap_or("0");
    format!("{}:leg:{}", intent.intent_id, ordinal)
}

fn scheduled_order_due(
    intent: &ExecuteStrategyIntent,
    orders: &[SubmitOrder],
    now_unix_nanos: u64,
) -> BTreeMap<OrderId, UnixNanos> {
    let mut last_due: BTreeMap<String, u64> = BTreeMap::new();
    let mut due = BTreeMap::new();
    for order in orders {
        let leg_id = intent_leg_id(intent, order);
        let interval = order
            .options
            .split
            .as_ref()
            .and_then(|policy| policy.interval)
            .or_else(|| {
                order
                    .options
                    .maker
                    .as_ref()
                    .and_then(|policy| policy.min_interval)
            })
            .unwrap_or(DurationNanos::new(0));
        let window_cadence = order
            .options
            .maker
            .as_ref()
            .and_then(|policy| policy.max_orders_per_window.zip(policy.window))
            .map(|(maximum, window)| DurationNanos::new(window.get().div_ceil(u64::from(maximum))))
            .unwrap_or(DurationNanos::new(0));
        let interval_nanos = interval.get().max(window_cadence.get());
        let next = last_due
            .get(&leg_id)
            .copied()
            .unwrap_or(now_unix_nanos)
            .saturating_add(if last_due.contains_key(&leg_id) {
                interval_nanos
            } else {
                0
            });
        due.insert(order.order_id.clone(), next.into());
        last_due.insert(leg_id, next);
    }
    due
}

fn template_options(intent: &ExecuteStrategyIntent, leg_id: &str) -> ExecutionOrderOptions {
    intent
        .legs
        .iter()
        .find(|leg| leg.leg_id == leg_id)
        .map(|leg| leg.options.clone())
        .unwrap_or_else(|| intent.order_options.clone())
}

fn build_single_intent_plan(
    intent: &ExecuteStrategyIntent,
    orders: &[SubmitOrder],
) -> Result<ExecutionPlan, ExecutionError> {
    let mut grouped: Vec<(String, Vec<&SubmitOrder>)> = Vec::new();
    for order in orders {
        let leg_id = intent_leg_id(intent, order);
        if let Some((_, values)) = grouped.iter_mut().find(|(id, _)| *id == leg_id) {
            values.push(order);
        } else {
            grouped.push((leg_id, vec![order]));
        }
    }
    let legs = grouped
        .into_iter()
        .map(|(leg_id, leg_orders)| {
            let order = leg_orders[0];
            let target_quantity = leg_orders
                .iter()
                .try_fold(Quantity::ZERO, |total, order| {
                    total.checked_add(order.quantity)
                })
                .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
            let mut leg = ExecutionLeg::new(
                leg_id,
                order.account_id.to_string(),
                order.segment_key.to_string(),
                order.instrument_id.to_string(),
                order.side,
                target_quantity,
            )
            .map_err(ExecutionError::Invalid)?;
            leg.market_id = order.market_id.clone();
            Ok(leg)
        })
        .collect::<Result<Vec<_>, ExecutionError>>()?;
    ExecutionPlan::new(
        format!("{}:plan:1", intent.intent_id),
        intent.intent_id.to_string(),
        intent.intent_type,
        legs,
        intent.completion_policy,
        intent.failure_policy,
    )
    .map_err(ExecutionError::Invalid)
}

fn apply_connection_event(
    order: &mut ExecutionOrder,
    event: OrderEntryEvent,
) -> Result<(), ExecutionError> {
    order.remote_order_id = event.remote_order_id;
    order.updated_at_unix_nanos = event.occurred_at_unix_nanos;
    order.reason = event.reason;
    order.status = match event.status {
        OrderEntryStatus::Accepted => ExecutionOrderStatus::Accepted,
        OrderEntryStatus::PartiallyFilled => ExecutionOrderStatus::PartiallyFilled,
        OrderEntryStatus::Filled => ExecutionOrderStatus::Filled,
        OrderEntryStatus::Canceled => ExecutionOrderStatus::Canceled,
        OrderEntryStatus::Rejected => ExecutionOrderStatus::Rejected,
        OrderEntryStatus::Expired => ExecutionOrderStatus::Expired,
        OrderEntryStatus::Unknown => ExecutionOrderStatus::Unknown,
    };
    Ok(())
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

fn remote_order(order: kairos_integration::application::ExternalOrder) -> RemoteOrder {
    RemoteOrder {
        binding_id: order.binding_id,
        order_id: order.order_id,
        client_order_id: order.client_order_id,
        symbol: order.symbol,
        side: match order.side {
            kairos_integration::application::OrderSide::Buy => OrderSide::Buy,
            kairos_integration::application::OrderSide::Sell => OrderSide::Sell,
        },
        order_type: match order.order_type {
            kairos_integration::application::OrderType::Market => OrderType::Market,
            _ => OrderType::Limit,
        },
        status: remote_status(&format!("{:?}", order.status)),
        quantity: order.quantity.try_into().expect("normalized quantity"),
        filled_quantity: order
            .filled_quantity
            .try_into()
            .expect("normalized filled quantity"),
        average_fill_price: order
            .average_fill_price
            .map(|value| value.try_into().expect("normalized average price")),
        occurred_at_unix_nanos: order.occurred_at_unix_millis,
    }
}

fn remote_execution_event(
    event: kairos_integration::application::ExternalExecutionEvent,
) -> RemoteOrderUpdate {
    RemoteOrderUpdate {
        order_id: event.order_id,
        symbol: event.symbol,
        status: remote_status(&format!("{:?}", event.status)),
        fill_quantity: event
            .fill_quantity
            .and_then(|value| format_decimal(value).parse().ok()),
        fill_price: event
            .fill_price
            .and_then(|value| format_decimal(value).parse().ok()),
        execution_id: event.execution_id,
        fee_currency: event.fee_currency,
        fee_amount: event
            .fee_amount
            .and_then(|value| format_decimal(value).parse().ok()),
        occurred_at_unix_nanos: event.occurred_at_unix_nanos,
        reason: event.reason,
    }
}

fn format_decimal(value: kairos_integration::application::DecimalValue) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.unsigned_abs().to_string();
    let scale = value.scale as usize;
    let padded = format!("{digits:0>width$}", width = scale + 1);
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}

fn parse_decimal(value: &str) -> Result<(i64, u8), ExecutionError> {
    let value = value.trim();
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
    let mantissa = format!("{whole}{fraction}")
        .parse::<i64>()
        .map_err(|_| ExecutionError::Invalid(format!("invalid decimal value: {value}")))?;
    Ok((
        if negative { -mantissa } else { mantissa },
        fraction.len() as u8,
    ))
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

fn audit_matches(event: &ExecutionAuditEvent, query: &ExecutionAuditQuery) -> bool {
    query
        .order_id
        .as_deref()
        .is_none_or(|value| event.order_id.as_str() == value)
        && query.remote_order_id.as_deref().is_none_or(|value| {
            event
                .remote_order_id
                .as_ref()
                .is_some_and(|remote_id| remote_id.as_str() == value)
        })
        && query
            .status
            .as_deref()
            .is_none_or(|value| format!("{:?}", event.status).eq_ignore_ascii_case(value))
        && query
            .since_unix_nanos
            .is_none_or(|value| event.occurred_at_unix_nanos >= value)
        && query
            .until_unix_nanos
            .is_none_or(|value| event.occurred_at_unix_nanos <= value)
}
