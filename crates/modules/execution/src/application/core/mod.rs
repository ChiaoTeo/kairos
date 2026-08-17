use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::{
    ActorId, Currency, DurationNanos, ExecutionRouteId, FillId, IntentId, Money, OrderId, Price,
    Quantity, RemoteOrderId, StrategyId, UnixNanos,
};

use super::model::*;
use super::RemoteOrderUpdate;
use crate::domain::{
    split_quantity, CommitmentBasis, CommitmentResource, CommitmentStatus, CompletionPolicy,
    ExecutionFill, ExecutionLeg, ExecutionOrder, ExecutionOrderStatus, ExecutionPlan,
    FailurePolicy, HedgePolicy, IntentType, MakerExecutionPolicy, OrderCommitment, OrderSide,
    OrderType, RiskReservationEvidence, RiskReservationSagaStatus, SplitOrderPolicy,
};
use crate::services::audit::{ExecutionAuditEvent, ExecutionAuditQuery};
use crate::services::dependencies::{ExecutionOrderAdmissionService, QueuedExecutionIntentPlanner};
use crate::services::risk::QueuedExecutionRiskReservations;

fn typed_intent_id(value: impl Into<String>) -> IntentId {
    IntentId::new(value).expect("validated intent ID")
}

fn typed_strategy_id(value: impl Into<String>) -> StrategyId {
    StrategyId::new(value).expect("validated strategy ID")
}

fn completed_quantity(intent: &ExecuteStrategyIntent, mantissa: i64) -> Quantity {
    Quantity::new(mantissa, intent.target_quantity.scale()).expect("completed quantity is valid")
}

fn simulation_commitment(
    request: &SubmitOrder,
    now: u64,
) -> Result<OrderCommitment, ExecutionError> {
    let mut commitment = OrderCommitment::new(
        request.order_id.clone(),
        request.account_id.clone(),
        request.segment_key.clone(),
        request.instrument_id.clone(),
        request.side,
        CommitmentResource::Instrument(request.instrument_id.clone()),
        Money::new(request.quantity.mantissa(), request.quantity.scale())
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
        request.quantity,
        CommitmentBasis::SimulationQuantity,
        now.into(),
    )
    .map_err(ExecutionError::Invalid)?;
    commitment.settlement_asset = request
        .options
        .quote_asset
        .as_deref()
        .map(Currency::new)
        .transpose()
        .map_err(|error| ExecutionError::Invalid(error.to_string()))?;
    Ok(commitment)
}

fn simulation_risk_reservation(
    request: &SubmitOrder,
    now: u64,
) -> Result<RiskReservationEvidence, ExecutionError> {
    Ok(RiskReservationEvidence {
        order_id: request.order_id.clone(),
        reservation_id: format!("execution:{}", request.order_id),
        idempotency_key: format!("execution:{}", request.order_id),
        account_id: request.account_id.clone(),
        amount: Money::new(request.quantity.mantissa(), request.quantity.scale())
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
        status: RiskReservationSagaStatus::Active,
        risk_generation: 0,
        risk_event_sequence: 0,
        policy_version: 0,
        expires_at_unix_nanos: u64::MAX.into(),
        updated_at_unix_nanos: now.into(),
    })
}

fn planned_risk_reservation(
    request: &SubmitOrder,
    commitment: &OrderCommitment,
    watermarks: &DependencyWatermarks,
    now: u64,
) -> RiskReservationEvidence {
    let risk = watermarks.risk.clone().unwrap_or_default();
    RiskReservationEvidence {
        order_id: request.order_id.clone(),
        reservation_id: format!("execution:{}", request.order_id),
        idempotency_key: format!("execution:{}", request.order_id),
        account_id: request.account_id.clone(),
        amount: commitment.amount,
        status: RiskReservationSagaStatus::AuthorizePending,
        risk_generation: risk.generation.get(),
        risk_event_sequence: risk.event_sequence.get(),
        policy_version: 0,
        expires_at_unix_nanos: UnixNanos::new(0),
        updated_at_unix_nanos: now.into(),
    }
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

mod intents;
pub(crate) mod orders;
mod queries;
mod reconciliation;

pub struct ExecutionApplication {
    actor_id: String,
    actor: crate::services::actor::ExecutionActor,
    pending_business_events: std::collections::VecDeque<ExecutionBusinessEvent>,
    execution_routes: BTreeMap<ExecutionRouteId, ConfiguredExecutionRoute>,
    order_entry: Option<Box<dyn OrderEntryConnection>>,
    order_query: Option<Box<dyn OrderQueryConnection>>,
    execution_stream: Option<Box<dyn OrderEventSource>>,
    store: Option<Box<dyn ExecutionStateStore>>,
    intent_planner: Option<QueuedExecutionIntentPlanner>,
    order_admission: Option<ExecutionOrderAdmissionService>,
    risk_reservations: Option<QueuedExecutionRiskReservations>,
    live_trading: bool,
    live_confirmed: bool,
    writer_recovery_ready: bool,
    risk_recovery_ready: bool,
    risk_recovery_error: Option<String>,
}

struct ConfiguredExecutionRoute {
    candidate: ExecutionRouteCandidate,
    provider_instrument: kairos_integration::application::ProviderInstrumentRef,
}

/// Concrete process wiring selected by Execution composition.
///
/// The entry and query connections may themselves route across any number of
/// account/segment/provider bindings. They are deliberately not part of the
/// public application contract.
pub(crate) struct ExecutionApplicationWiring {
    pub(crate) order_entry_gateway: Option<Box<dyn OrderEntryConnection>>,
    pub(crate) order_query_gateway: Option<Box<dyn OrderQueryConnection>>,
    pub(crate) legacy_execution_stream: Option<Box<dyn OrderEventSource>>,
    pub(crate) state_store: Option<Box<dyn ExecutionStateStore>>,
}

impl ExecutionApplication {
    /// Advance the replay business clock and its composition-owned
    /// dependencies.  The application remains the state owner; concrete
    /// Dependency reads stay behind the intent-planning boundary.
    pub fn advance_time(&mut self, event_time_unix_nanos: u64) -> Result<(), ExecutionError> {
        if let Some(intent_planner) = self.intent_planner.as_mut() {
            intent_planner
                .advance_time(event_time_unix_nanos)
                .map_err(ExecutionError::Invalid)?;
        }
        Ok(())
    }
    pub(crate) fn assemble(
        actor_id: impl Into<String>,
        wiring: ExecutionApplicationWiring,
    ) -> Result<Self, ExecutionError> {
        let actor_id = actor_id.into();
        if actor_id.trim().is_empty() {
            return Err(ExecutionError::Invalid("actor_id is required".into()));
        }
        let mut application = Self {
            actor_id,
            actor: crate::services::actor::ExecutionActor::new(),
            pending_business_events: std::collections::VecDeque::new(),
            execution_routes: BTreeMap::new(),
            order_entry: wiring.order_entry_gateway,
            order_query: wiring.order_query_gateway,
            execution_stream: wiring.legacy_execution_stream,
            store: wiring.state_store,
            intent_planner: None,
            order_admission: None,
            risk_reservations: None,
            live_trading: false,
            live_confirmed: false,
            writer_recovery_ready: true,
            risk_recovery_ready: true,
            risk_recovery_error: None,
        };
        if let Some(store) = application.store.as_mut() {
            if let Some(snapshot) = store.load().map_err(ExecutionError::Persistence)? {
                application.actor.restore(
                    snapshot.generation.get(),
                    snapshot.event_sequence.get(),
                    snapshot.orders,
                    snapshot.commitments,
                    snapshot.risk_reservations,
                    snapshot.events,
                    snapshot.fills,
                    snapshot.unknown_remote_orders,
                    snapshot.exchange_event_watermark_unix_nanos.get(),
                );
                application
                    .actor
                    .restore_intents(snapshot.intents, snapshot.intent_idempotency);
                application
                    .actor
                    .restore_intent_events(snapshot.intent_events);
                info!(
                    event = "execution_state_restored",
                    component = "execution",
                    generation = application.actor.generation(),
                    event_sequence = application.actor.event_sequence(),
                    order_count = application.actor.order_map().len(),
                    intent_count = application.actor.intent_count(),
                    fill_count = application.actor.fills().len(),
                    "execution state restored from persistence"
                );
            }
        }
        Ok(application)
    }

    #[cfg(test)]
    pub(crate) fn assemble_for_test(
        actor_id: impl Into<String>,
        order_entry: Option<Box<dyn OrderEntryConnection>>,
        state_store: Option<Box<dyn ExecutionStateStore>>,
    ) -> Result<Self, ExecutionError> {
        Self::assemble(
            actor_id,
            ExecutionApplicationWiring {
                order_entry_gateway: order_entry,
                order_query_gateway: None,
                legacy_execution_stream: None,
                state_store,
            },
        )
    }

    #[cfg(test)]
    pub(crate) fn assemble_for_test_with_query(
        actor_id: impl Into<String>,
        order_entry: Option<Box<dyn OrderEntryConnection>>,
        order_query: Option<Box<dyn OrderQueryConnection>>,
        state_store: Option<Box<dyn ExecutionStateStore>>,
    ) -> Result<Self, ExecutionError> {
        Self::assemble(
            actor_id,
            ExecutionApplicationWiring {
                order_entry_gateway: order_entry,
                order_query_gateway: order_query,
                legacy_execution_stream: None,
                state_store,
            },
        )
    }

    #[cfg(test)]
    pub(crate) fn assemble_for_test_with_query_and_stream(
        actor_id: impl Into<String>,
        order_entry: Option<Box<dyn OrderEntryConnection>>,
        order_query: Option<Box<dyn OrderQueryConnection>>,
        execution_stream: Option<Box<dyn OrderEventSource>>,
        state_store: Option<Box<dyn ExecutionStateStore>>,
    ) -> Result<Self, ExecutionError> {
        Self::assemble(
            actor_id,
            ExecutionApplicationWiring {
                order_entry_gateway: order_entry,
                order_query_gateway: order_query,
                legacy_execution_stream: execution_stream,
                state_store,
            },
        )
    }

    /// Install one configured, Execution-owned route and its provider address.
    pub(crate) fn configure_execution_route(
        &mut self,
        candidate: ExecutionRouteCandidate,
        provider_instrument: kairos_integration::application::ProviderInstrumentRef,
    ) {
        self.execution_routes.insert(
            candidate.route_id.clone(),
            ConfiguredExecutionRoute {
                candidate,
                provider_instrument,
            },
        );
    }

    pub fn available_execution_routes(
        &self,
        query: &ExecutionRouteQuery,
    ) -> Vec<ExecutionRouteCandidate> {
        self.execution_routes
            .values()
            .map(|route| &route.candidate)
            .filter(|candidate| query.matches(candidate))
            .cloned()
            .collect()
    }

    pub fn snapshot(&self) -> ExecutionSnapshot {
        ExecutionSnapshot {
            actor_id: ActorId::new(self.actor_id.clone()).expect("validated actor ID"),
            generation: self.actor.generation().into(),
            event_sequence: self.actor.event_sequence().into(),
            orders: self.actor.order_map().values().cloned().collect(),
            events: self.actor.events().to_vec(),
            fills: self.actor.fills().to_vec(),
            commitments: self.actor.commitments().cloned().collect(),
            risk_reservations: self.actor.risk_reservations().cloned().collect(),
            intents: self.actor.intents().cloned().collect(),
            intent_events: self.actor.intent_events().to_vec(),
            intent_idempotency: self.actor.intent_idempotency().clone(),
            unknown_remote_orders: self.actor.unknown_remote_orders().cloned().collect(),
            exchange_event_watermark_unix_nanos: self.actor.remote_watermark().into(),
        }
    }

    pub fn current_view(&self) -> ExecutionCurrentView {
        ExecutionCurrentView {
            generation: self.actor.generation().into(),
            event_sequence: self.actor.event_sequence().into(),
            orders: self.actor.order_map().values().cloned().collect(),
            commitments: self.actor.commitments().cloned().collect(),
            risk_reservations: self.actor.risk_reservations().cloned().collect(),
            intents: self.actor.intents().cloned().collect(),
            events: self.actor.events().to_vec(),
            intent_events: self.actor.intent_events().to_vec(),
            fills: self.actor.fills().to_vec(),
            unknown_remote_orders: self.actor.unknown_remote_orders().cloned().collect(),
            exchange_event_watermark_unix_nanos: self.actor.remote_watermark().into(),
        }
    }

    pub fn event_sequence(&self) -> u64 {
        self.actor.event_sequence()
    }

    pub fn drain_events(&mut self) -> Vec<ExecutionEvent> {
        self.actor.drain_events()
    }

    pub(crate) fn pending_business_event(&self) -> Option<&ExecutionBusinessEvent> {
        self.pending_business_events.front()
    }

    pub(crate) fn acknowledge_business_event(&mut self) {
        self.pending_business_events.pop_front();
    }

    pub(crate) fn configure_live_trading(&mut self, enabled: bool, confirmed: bool) {
        self.live_trading = enabled;
        self.live_confirmed = confirmed;
        // A live writer must reconcile provider state after acquiring its
        // fenced lease before any new order can be admitted. Paper/backtest
        // has no provider writer takeover and starts ready.
        self.writer_recovery_ready = !enabled;
        if enabled
            && self.actor.risk_reservations().any(|value| {
                matches!(
                    value.status,
                    RiskReservationSagaStatus::AuthorizePending
                        | RiskReservationSagaStatus::ResizePending
                        | RiskReservationSagaStatus::ReleasePending
                        | RiskReservationSagaStatus::ConsumePending
                        | RiskReservationSagaStatus::Uncertain
                )
            })
        {
            self.risk_recovery_ready = false;
        }
    }

    pub fn complete_writer_reconciliation(&mut self) {
        self.writer_recovery_ready = true;
    }

    pub fn writer_recovery_ready(&self) -> bool {
        self.writer_recovery_ready
    }

    pub(crate) fn attach_intent_planner(&mut self, planner: QueuedExecutionIntentPlanner) {
        self.intent_planner = Some(planner);
    }

    pub(crate) fn attach_order_admission(&mut self, admission: ExecutionOrderAdmissionService) {
        self.order_admission = Some(admission);
    }

    pub(crate) fn attach_risk_reservations(
        &mut self,
        risk_reservations: QueuedExecutionRiskReservations,
    ) {
        self.risk_reservations = Some(risk_reservations);
    }

    /// Resolve every durable in-flight Risk saga exclusively from Risk's
    /// typed mmap current view. A missing or stale observation keeps the live
    /// admission barrier closed; recovery never retries an uncertain money or
    /// capacity command merely because Execution restarted.
    pub(crate) fn recover_risk_reservations(&mut self) -> Result<(), ExecutionError> {
        let pending: Vec<_> = self
            .actor
            .risk_reservations()
            .filter(|value| {
                matches!(
                    value.status,
                    RiskReservationSagaStatus::AuthorizePending
                        | RiskReservationSagaStatus::ResizePending
                        | RiskReservationSagaStatus::ReleasePending
                        | RiskReservationSagaStatus::ConsumePending
                        | RiskReservationSagaStatus::Uncertain
                )
            })
            .cloned()
            .collect();
        if pending.is_empty() {
            self.risk_recovery_ready = true;
            self.risk_recovery_error = None;
            return Ok(());
        }
        let risk_reservations = self.risk_reservations.as_mut().ok_or_else(|| {
            ExecutionError::Invalid("Risk recovery requires a reservation adapter".into())
        })?;
        for evidence in pending {
            let observed = risk_reservations
                .reconcile(&evidence)
                .map_err(|error| {
                    self.risk_recovery_ready = false;
                    self.risk_recovery_error = Some(error.clone());
                    ExecutionError::Invalid(format!("Risk recovery is not ready: {error}"))
                })?
                .ok_or_else(|| {
                    let error = format!(
                        "Risk mmap has no reservation {} at or after event sequence {}",
                        evidence.reservation_id, evidence.risk_event_sequence
                    );
                    self.risk_recovery_ready = false;
                    self.risk_recovery_error = Some(error.clone());
                    ExecutionError::Invalid(error)
                })?;
            if observed.risk_event_sequence < evidence.risk_event_sequence {
                let error = format!(
                    "Risk mmap watermark {} precedes durable Execution evidence {}",
                    observed.risk_event_sequence, evidence.risk_event_sequence
                );
                self.risk_recovery_ready = false;
                self.risk_recovery_error = Some(error.clone());
                return Err(ExecutionError::Invalid(error));
            }
            self.actor.reconcile_risk_reservation(observed);
        }
        self.persist_snapshot()?;
        self.risk_recovery_ready = true;
        self.risk_recovery_error = None;
        Ok(())
    }

    pub fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.order_admission
            .as_ref()
            .map(|admission| admission.dependency_watermarks())
            .or_else(|| {
                self.intent_planner
                    .as_ref()
                    .map(|planner| planner.dependency_watermarks())
            })
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
}

fn to_connection_request(
    order: &ExecutionOrder,
    segment_key: &str,
    options: &ExecutionOrderOptions,
    execution_routes: &BTreeMap<ExecutionRouteId, ConfiguredExecutionRoute>,
) -> Result<OrderEntryRequest, String> {
    let access_id = order.execution_route_id.as_ref().ok_or_else(|| {
        "execution_route_id is required; provider identity is not inferred".to_string()
    })?;
    let provider_instrument = execution_routes
        .get(access_id)
        .map(|route| route.provider_instrument.clone())
        .ok_or_else(|| format!("execution access is not configured: {access_id}"))?;
    Ok(OrderEntryRequest {
        order_id: order.order_id.clone(),
        intent_id: order.intent_id.clone(),
        account_id: order.account_id.clone(),
        segment_key: kairos_primitives::SegmentKey::new(segment_key)
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

pub(crate) fn apply_connection_event(
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
