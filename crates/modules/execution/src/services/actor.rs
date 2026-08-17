//! Runtime input facts for the Execution state owner.
//!
//! Commands and exchange facts are deliberately different inputs. Commands are
//! requests from callers and have a reply path; exchange events are facts from a
//! private order stream and are consumed independently by the state owner.

use std::collections::{BTreeMap, HashSet, VecDeque};

use crate::application::{
    ExecutionEvent, ExecutionFillReport, IntentEvent, IntentState, RemoteOrderUpdate, SubmitOrder,
    UnknownRemoteOrder, UnknownRemoteOrderResolution,
};
use crate::domain::{
    CommitmentBasis, CommitmentStatus, ExecutionFill, ExecutionOrder, ExecutionOrderStatus,
    LegLifecycle, Money, OrderCommitment, OrderId, Quantity, RiskReservationEvidence,
    RiskReservationSagaStatus, UnixNanos,
};
use kairos_integration::application::OrderEntryEvent;

/// A normalized order/fill fact produced by the exchange's private order stream.
///
/// This is not an application command and must never be mixed with Market,
/// Reference, Account, or Risk events.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RemoteOrderEvent {
    pub event_id: String,
    pub connection_id: String,
    pub event: RemoteOrderUpdate,
}

const MAX_SEEN_EXCHANGE_EVENTS: usize = 100_000;

/// The single owner of mutable order-lifecycle state.
///
/// Provider I/O, persistence I/O and cross-module clients deliberately stay
/// outside this type. Application initially drives these fields while each
/// command slice is migrated to typed Actor transitions.
pub(crate) struct ExecutionActor {
    generation: u64,
    event_sequence: u64,
    orders: BTreeMap<OrderId, ExecutionOrder>,
    commitments: BTreeMap<OrderId, OrderCommitment>,
    risk_reservations: BTreeMap<OrderId, RiskReservationEvidence>,
    intents: BTreeMap<String, IntentState>,
    intent_events: Vec<IntentEvent>,
    pending_intent_events: Vec<IntentEvent>,
    intent_idempotency: BTreeMap<String, String>,
    events: Vec<ExecutionEvent>,
    pending_events: Vec<ExecutionEvent>,
    fills: Vec<ExecutionFill>,
    unknown_remote_orders: BTreeMap<String, UnknownRemoteOrder>,
    exchange_event_watermark_unix_nanos: u64,
    seen_exchange_events: HashSet<String>,
    exchange_event_order: VecDeque<String>,
}

pub(crate) enum FillTransition {
    Duplicate(ExecutionOrder),
    Conflict(ExecutionFill),
    Applied {
        order: ExecutionOrder,
        fill: ExecutionFill,
        event: ExecutionEvent,
    },
}

impl ExecutionActor {
    pub(crate) fn new() -> Self {
        Self {
            generation: 0,
            event_sequence: 0,
            orders: BTreeMap::new(),
            commitments: BTreeMap::new(),
            risk_reservations: BTreeMap::new(),
            intents: BTreeMap::new(),
            intent_events: Vec::new(),
            pending_intent_events: Vec::new(),
            intent_idempotency: BTreeMap::new(),
            events: Vec::new(),
            pending_events: Vec::new(),
            fills: Vec::new(),
            unknown_remote_orders: BTreeMap::new(),
            exchange_event_watermark_unix_nanos: 0,
            seen_exchange_events: HashSet::new(),
            exchange_event_order: VecDeque::new(),
        }
    }

    pub(crate) fn accept_exchange_event(&mut self, event_id: &str) -> bool {
        if !self.seen_exchange_events.insert(event_id.to_owned()) {
            return false;
        }
        self.exchange_event_order.push_back(event_id.to_owned());
        if self.exchange_event_order.len() > MAX_SEEN_EXCHANGE_EVENTS {
            if let Some(expired) = self.exchange_event_order.pop_front() {
                self.seen_exchange_events.remove(&expired);
            }
        }
        true
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn restore(
        &mut self,
        generation: u64,
        event_sequence: u64,
        orders: Vec<ExecutionOrder>,
        commitments: Vec<OrderCommitment>,
        risk_reservations: Vec<RiskReservationEvidence>,
        events: Vec<ExecutionEvent>,
        fills: Vec<ExecutionFill>,
        unknown_remote_orders: Vec<UnknownRemoteOrder>,
        exchange_event_watermark_unix_nanos: u64,
    ) {
        self.generation = generation;
        self.event_sequence = event_sequence;
        self.orders = orders
            .into_iter()
            .map(|order| (order.order_id.clone(), order))
            .collect();
        self.commitments = commitments
            .into_iter()
            .map(|commitment| (commitment.order_id.clone(), commitment))
            .collect();
        self.risk_reservations = risk_reservations
            .into_iter()
            .map(|reservation| (reservation.order_id.clone(), reservation))
            .collect();
        self.events = events;
        self.pending_events = self.events.clone();
        self.fills = fills;
        self.unknown_remote_orders = unknown_remote_orders
            .into_iter()
            .map(|order| (order.remote_order_id.to_string(), order))
            .collect();
        self.exchange_event_watermark_unix_nanos = exchange_event_watermark_unix_nanos;
    }

    pub(crate) fn generation(&self) -> u64 {
        self.generation
    }

    pub(crate) fn event_sequence(&self) -> u64 {
        self.event_sequence
    }

    pub(crate) fn order_map(&self) -> &BTreeMap<OrderId, ExecutionOrder> {
        &self.orders
    }

    pub(crate) fn commitments(&self) -> impl Iterator<Item = &OrderCommitment> {
        self.commitments.values()
    }

    pub(crate) fn commitment(&self, order_id: &str) -> Option<&OrderCommitment> {
        self.commitments.get(order_id)
    }

    pub(crate) fn risk_reservations(&self) -> impl Iterator<Item = &RiskReservationEvidence> {
        self.risk_reservations.values()
    }

    pub(crate) fn risk_reservation(&self, order_id: &str) -> Option<&RiskReservationEvidence> {
        self.risk_reservations.get(order_id)
    }

    pub(crate) fn events(&self) -> &[ExecutionEvent] {
        &self.events
    }

    pub(crate) fn fills(&self) -> &[ExecutionFill] {
        &self.fills
    }

    pub(crate) fn intent_events(&self) -> &[IntentEvent] {
        &self.intent_events
    }

    pub(crate) fn drain_intent_events(&mut self) -> Vec<IntentEvent> {
        std::mem::take(&mut self.pending_intent_events)
    }

    pub(crate) fn intents(&self) -> impl Iterator<Item = &IntentState> {
        self.intents.values()
    }

    pub(crate) fn intent_count(&self) -> usize {
        self.intents.len()
    }

    pub(crate) fn intent(&self, intent_id: &str) -> Option<&IntentState> {
        self.intents.get(intent_id)
    }

    pub(crate) fn contains_intent(&self, intent_id: &str) -> bool {
        self.intents.contains_key(intent_id)
    }

    pub(crate) fn restore_intents(
        &mut self,
        intents: Vec<IntentState>,
        idempotency: BTreeMap<String, String>,
    ) {
        self.intents = intents
            .into_iter()
            .map(|state| (state.intent.intent_id.to_string(), state))
            .collect();
        self.intent_idempotency = idempotency;
    }

    pub(crate) fn insert_intent(&mut self, state: IntentState) {
        self.intents
            .insert(state.intent.intent_id.to_string(), state);
    }

    pub(crate) fn intent_for_idempotency_key(
        &self,
        key: &str,
    ) -> Result<Option<&IntentState>, String> {
        self.intent_idempotency
            .get(key)
            .map(|intent_id| {
                self.intents
                    .get(intent_id)
                    .ok_or_else(|| "idempotency record references missing intent".to_string())
            })
            .transpose()
    }

    pub(crate) fn record_intent_idempotency(&mut self, key: String, intent_id: String) {
        self.intent_idempotency.insert(key, intent_id);
    }

    pub(crate) fn intent_idempotency(&self) -> &BTreeMap<String, String> {
        &self.intent_idempotency
    }

    pub(crate) fn increment_compensation_attempts(&mut self, intent_id: &str) {
        if let Some(state) = self.intents.get_mut(intent_id) {
            state.compensation_attempts = state.compensation_attempts.saturating_add(1);
        }
    }

    pub(crate) fn remove_pending_order(&mut self, intent_id: &str, order_id: &OrderId) {
        if let Some(state) = self.intents.get_mut(intent_id) {
            state
                .pending_orders
                .retain(|order| &order.order_id != order_id);
            state.pending_order_due_unix_nanos.remove(order_id);
        }
    }

    pub(crate) fn clear_pending_orders(&mut self, intent_id: &str) {
        if let Some(state) = self.intents.get_mut(intent_id) {
            state.pending_orders.clear();
            state.pending_order_due_unix_nanos.clear();
        }
    }

    pub(crate) fn update_quote_refresh(&mut self, intent_id: &str, version: u64, now: u64) {
        if let Some(state) = self.intents.get_mut(intent_id) {
            state.quote_version = version;
            state.last_quote_refresh_unix_nanos = Some(now.into());
        }
    }

    pub(crate) fn attach_intent_plan_order(
        &mut self,
        intent_id: &str,
        leg_id: &str,
        order_id: &str,
    ) -> Result<(), String> {
        let (plan_id, leg_id) = {
            let plan = self
                .intents
                .get_mut(intent_id)
                .ok_or_else(|| "intent plan owner is missing".to_string())?
                .plan
                .as_mut()
                .ok_or_else(|| "intent has no execution plan".to_string())?;
            let plan_id = plan.plan_id.clone();
            let leg = plan
                .legs
                .iter_mut()
                .find(|leg| leg.leg_id == leg_id)
                .ok_or_else(|| "intent plan leg is missing".to_string())?;
            if !leg.order_ids.iter().any(|value| value == order_id) {
                leg.order_ids.push(OrderId::new(order_id.to_owned())?);
            }
            if leg.lifecycle == LegLifecycle::Pending {
                leg.transition(LegLifecycle::Ready, "child order prepared")?;
            }
            if leg.lifecycle == LegLifecycle::Ready {
                leg.transition(LegLifecycle::Executing, "child order submitted")?;
            }
            (plan_id, leg.leg_id.clone())
        };
        self.attach_plan_identity(order_id, plan_id, leg_id);
        Ok(())
    }

    pub(crate) fn refresh_intent_plan_progress(
        &mut self,
        intent_id: &str,
        orders: &[ExecutionOrder],
    ) -> Result<(), String> {
        let Some(plan) = self
            .intents
            .get_mut(intent_id)
            .and_then(|state| state.plan.as_mut())
        else {
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
                .map_err(|_| "execution leg completed quantity overflow".to_string())?;
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
            leg.transition(next, "child order progress updated")?;
        }
        Ok(())
    }

    pub(crate) fn restore_intent_events(&mut self, events: Vec<IntentEvent>) {
        self.intent_events = events;
        self.pending_intent_events = self.intent_events.clone();
    }

    /// Apply one intent lifecycle fact atomically inside the mutable state
    /// owner. Sequence allocation and both durable/current projections must
    /// never be assembled independently by the application facade.
    pub(crate) fn apply_intent_event(
        &mut self,
        mut event: IntentEvent,
    ) -> Result<(IntentEvent, IntentState), String> {
        let state = self
            .intents
            .get_mut(event.intent_id.as_str())
            .ok_or_else(|| "intent event references unknown intent".to_string())?;
        state.status = event.status;
        state.order_ids.extend(event.order_ids.iter().cloned());
        state.order_ids.sort();
        state.order_ids.dedup();
        state.completed_quantity = event.completed_quantity;
        state.updated_at_unix_nanos = event.occurred_at_unix_nanos;
        state.reason = event.reason.clone();
        self.event_sequence += 1;
        self.generation += 1;
        event.event_sequence = self.event_sequence.into();
        self.intent_events.push(event.clone());
        self.pending_intent_events.push(event.clone());
        Ok((event, state.clone()))
    }

    pub(crate) fn unknown_remote_orders(&self) -> impl Iterator<Item = &UnknownRemoteOrder> {
        self.unknown_remote_orders.values()
    }

    pub(crate) fn drain_events(&mut self) -> Vec<ExecutionEvent> {
        std::mem::take(&mut self.pending_events)
    }

    pub(crate) fn contains_order(&self, order_id: &str) -> bool {
        self.orders.contains_key(order_id)
    }

    pub(crate) fn order(&self, order_id: &str) -> Option<&ExecutionOrder> {
        self.orders.get(order_id)
    }

    pub(crate) fn prepare_submission(
        &mut self,
        request: &SubmitOrder,
        commitment: OrderCommitment,
        risk_reservation: RiskReservationEvidence,
        now: u64,
    ) -> Result<(ExecutionOrder, ExecutionEvent), String> {
        if self.contains_order(request.order_id.as_str()) {
            return Err("order_id already exists".into());
        }
        let mut order = ExecutionOrder::new(
            request.order_id.to_string(),
            request.account_id.to_string(),
            request.segment_key.to_string(),
            request.instrument_id.to_string(),
            request.side,
            request.order_type,
            request.quantity,
            now,
        )?;
        order.intent_id = request.intent_id.clone();
        order.strategy_id = request.strategy_id.clone();
        order.market_id = request.market_id.clone();
        order.execution_access_id = request.execution_access_id.clone();
        order.limit_price = request.limit_price;
        order.status = ExecutionOrderStatus::Pending;
        let event = order_event(&order, now, String::new());
        self.orders.insert(order.order_id.clone(), order.clone());
        self.commitments.insert(order.order_id.clone(), commitment);
        self.risk_reservations
            .insert(order.order_id.clone(), risk_reservation);
        Ok((order, event))
    }

    pub(crate) fn activate_submission(
        &mut self,
        order_id: &str,
        reservation: RiskReservationEvidence,
        now: u64,
    ) -> Result<(ExecutionOrder, ExecutionEvent), String> {
        let mut order = self
            .orders
            .get(order_id)
            .cloned()
            .ok_or_else(|| "unknown order".to_string())?;
        if order.status != ExecutionOrderStatus::Pending {
            return Err("order is not pending admission".into());
        }
        order.status = ExecutionOrderStatus::Submitting;
        order.updated_at_unix_nanos = now.into();
        order.reason.clear();
        self.orders.insert(order.order_id.clone(), order.clone());
        self.risk_reservations
            .insert(order.order_id.clone(), reservation);
        Ok((order.clone(), order_event(&order, now, String::new())))
    }

    pub(crate) fn set_risk_reservation_status(
        &mut self,
        order_id: &str,
        status: RiskReservationSagaStatus,
        now: u64,
    ) {
        if let Some(reservation) = self.risk_reservations.get_mut(order_id) {
            reservation.status = status;
            reservation.updated_at_unix_nanos = now.into();
        }
    }

    pub(crate) fn set_risk_reservation_amount(&mut self, order_id: &str, amount: Money, now: u64) {
        if let Some(reservation) = self.risk_reservations.get_mut(order_id) {
            reservation.amount = amount;
            reservation.updated_at_unix_nanos = now.into();
        }
    }

    pub(crate) fn reconcile_risk_reservation(&mut self, evidence: RiskReservationEvidence) {
        self.risk_reservations
            .insert(evidence.order_id.clone(), evidence);
    }

    pub(crate) fn set_commitment_status(
        &mut self,
        order_id: &str,
        status: CommitmentStatus,
        now: u64,
    ) {
        if let Some(commitment) = self.commitments.get_mut(order_id) {
            commitment.status = status;
            commitment.updated_at_unix_nanos = now.into();
        }
    }

    pub(crate) fn resize_commitment(
        &mut self,
        order_id: &str,
        remaining: Quantity,
        now: u64,
    ) -> Result<(), String> {
        let Some(commitment) = self.commitments.get_mut(order_id) else {
            return Ok(());
        };
        if remaining == Quantity::ZERO {
            commitment.remaining_quantity = remaining;
            commitment.amount = Money::ZERO;
            commitment.status = CommitmentStatus::Released;
            commitment.updated_at_unix_nanos = now.into();
            return Ok(());
        }
        let amount = match commitment.basis {
            CommitmentBasis::QuotePriceCap { price_cap } => remaining
                .checked_mul(price_cap)
                .map_err(|error| error.to_string())?,
            CommitmentBasis::ContractNotional { .. } => {
                return Err(
                    "contract commitment resizing requires product-specific semantics".into(),
                )
            }
            CommitmentBasis::BaseQuantity | CommitmentBasis::SimulationQuantity => {
                Money::new(remaining.mantissa(), remaining.scale())
                    .map_err(|error| error.to_string())?
            }
        };
        commitment.remaining_quantity = remaining;
        commitment.amount = amount;
        commitment.status = CommitmentStatus::Reduced;
        commitment.updated_at_unix_nanos = now.into();
        Ok(())
    }

    pub(crate) fn apply_order_entry_event(
        &mut self,
        order_id: &str,
        event: OrderEntryEvent,
    ) -> Result<(ExecutionOrder, ExecutionEvent), String> {
        let mut order = self
            .order(order_id)
            .cloned()
            .ok_or_else(|| "unknown order".to_string())?;
        let occurred_at = event.occurred_at_unix_nanos;
        crate::application::apply_connection_event(&mut order, event)
            .map_err(|error| error.to_string())?;
        if order.status == ExecutionOrderStatus::Accepted && order.remote_order_id.is_none() {
            order.status = ExecutionOrderStatus::Unknown;
            order.reason = "accepted order did not return a exchange order id".into();
        }
        let persisted = order_event(&order, occurred_at.get(), String::new());
        self.orders.insert(order.order_id.clone(), order.clone());
        Ok((order, persisted))
    }

    pub(crate) fn mark_delivery_status(
        &mut self,
        order_id: &str,
        status: ExecutionOrderStatus,
        reason: String,
        now: u64,
    ) -> Option<(ExecutionOrder, ExecutionEvent)> {
        let mut order = self.order(order_id)?.clone();
        order.status = status;
        order.reason = reason.clone();
        order.updated_at_unix_nanos = UnixNanos::new(now);
        let event = order_event(&order, now, reason);
        self.orders.insert(order.order_id.clone(), order.clone());
        Some((order, event))
    }

    pub(crate) fn record_fill(
        &mut self,
        request: &ExecutionFillReport,
        now: u64,
    ) -> Result<FillTransition, String> {
        if let Some(existing) = self
            .fills
            .iter()
            .find(|fill| fill.fill_id == request.fill_id)
            .cloned()
        {
            let same_fact = existing.order_id == request.order_id
                && existing.quantity == request.quantity
                && existing.price == request.price
                && existing.fee == request.fee
                && existing.fee_currency == request.fee_currency
                && request
                    .occurred_at_unix_nanos
                    .is_none_or(|value| value == existing.occurred_at_unix_nanos);
            if same_fact {
                let order = self
                    .order(request.order_id.as_str())
                    .cloned()
                    .ok_or_else(|| "duplicate fill references unknown order".to_string())?;
                return Ok(FillTransition::Duplicate(order));
            }
            return Ok(FillTransition::Conflict(existing));
        }
        if request.quantity.mantissa() <= 0 || request.price.mantissa() <= 0 {
            return Err("fill quantity and price must be positive".into());
        }
        if request.fee.mantissa() < 0 {
            return Err("fill fee cannot be negative".into());
        }
        let current = self
            .order(request.order_id.as_str())
            .cloned()
            .ok_or_else(|| "unknown order".to_string())?;
        if current.status.terminal() {
            return Err("order is terminal".into());
        }
        let filled = current
            .filled_quantity
            .checked_add(request.quantity)
            .map_err(|error| error.to_string())?;
        if filled > current.quantity {
            return Err("cumulative fill exceeds order quantity".into());
        }
        let mut order = current;
        order.filled_quantity = filled;
        order.updated_at_unix_nanos = UnixNanos::new(now);
        order.status = if filled == order.quantity {
            ExecutionOrderStatus::Filled
        } else {
            ExecutionOrderStatus::PartiallyFilled
        };
        let fill = ExecutionFill {
            fill_id: request.fill_id.clone(),
            order_id: order.order_id.clone(),
            plan_id: order.plan_id.clone(),
            leg_id: order.leg_id.clone(),
            intent_id: order.intent_id.clone(),
            instrument_id: order.instrument_id.clone(),
            execution_market_id: request
                .execution_market_id
                .clone()
                .or_else(|| order.market_id.clone()),
            side: order.side,
            quantity: request.quantity,
            price: request.price,
            fee: request.fee,
            fee_currency: request.fee_currency.clone(),
            occurred_at_unix_nanos: UnixNanos::new(now),
        };
        let mut event = order_event(&order, now, String::new());
        event.fill_id = Some(request.fill_id.clone());
        event.filled_quantity = Some(filled);
        self.orders.insert(order.order_id.clone(), order.clone());
        self.fills.push(fill.clone());
        Ok(FillTransition::Applied { order, fill, event })
    }

    pub(crate) fn observe_remote_time(&mut self, observed_at: u64) {
        self.exchange_event_watermark_unix_nanos =
            self.exchange_event_watermark_unix_nanos.max(observed_at);
    }

    pub(crate) fn remote_watermark(&self) -> u64 {
        self.exchange_event_watermark_unix_nanos
    }

    pub(crate) fn find_remote_order(
        &self,
        remote_or_client_order_id: &str,
    ) -> Option<ExecutionOrder> {
        self.orders
            .values()
            .find(|order| {
                order.order_id.as_str() == remote_or_client_order_id
                    || order.remote_order_id.as_deref() == Some(remote_or_client_order_id)
            })
            .cloned()
    }

    pub(crate) fn reconcile_order(
        &mut self,
        local_order_id: &str,
        remote_order_id: &str,
        status: ExecutionOrderStatus,
        occurred_at: u64,
        reason: String,
    ) -> Result<(ExecutionOrder, ExecutionEvent), String> {
        let mut order = self
            .order(local_order_id)
            .cloned()
            .ok_or_else(|| "reconciled order disappeared".to_string())?;
        order.remote_order_id = Some(crate::domain::RemoteOrderId::new(
            remote_order_id.to_owned(),
        )?);
        order.status = status;
        order.updated_at_unix_nanos = UnixNanos::new(occurred_at);
        order.reason = reason.clone();
        let event = order_event(&order, occurred_at, reason);
        self.orders.insert(order.order_id.clone(), order.clone());
        Ok((order, event))
    }

    pub(crate) fn resolve_unknown_remote_order(
        &mut self,
        remote_order_id: &str,
        resolution: UnknownRemoteOrderResolution,
        reason: String,
        now: u64,
    ) -> Result<(), String> {
        let order = self
            .unknown_remote_orders
            .get_mut(remote_order_id)
            .ok_or_else(|| format!("unknown remote order does not exist: {remote_order_id}"))?;
        order.resolution = resolution;
        order.reason = reason;
        order.last_seen_at_unix_nanos = now.into();
        Ok(())
    }

    pub(crate) fn link_unknown_remote_order(
        &mut self,
        remote_order_id: &str,
        local_order_id: &str,
    ) -> Result<(UnknownRemoteOrder, ExecutionOrder, ExecutionEvent), String> {
        let unknown = self
            .unknown_remote_orders
            .get(remote_order_id)
            .cloned()
            .ok_or_else(|| format!("unknown remote order does not exist: {remote_order_id}"))?;
        let (order, event) = self.reconcile_order(
            local_order_id,
            remote_order_id,
            unknown.status,
            unknown.last_seen_at_unix_nanos.get(),
            "linked from unknown remote order reconciliation".into(),
        )?;
        self.resolve_unknown_remote_order(
            remote_order_id,
            UnknownRemoteOrderResolution::LinkedToLocalOrder,
            format!("linked to local order {local_order_id}"),
            unknown.last_seen_at_unix_nanos.get(),
        )?;
        Ok((unknown, order, event))
    }

    pub(crate) fn attach_plan_identity(
        &mut self,
        order_id: &str,
        plan_id: crate::domain::PlanId,
        leg_id: crate::domain::LegId,
    ) {
        if let Some(order) = self.orders.get_mut(order_id) {
            order.plan_id = Some(plan_id);
            order.leg_id = Some(leg_id);
        }
    }

    pub(crate) fn commit_event(&mut self, event: ExecutionEvent) -> ExecutionEvent {
        self.event_sequence += 1;
        self.generation += 1;
        self.events.push(event.clone());
        self.pending_events.push(event.clone());
        event
    }

    pub(crate) fn record_unknown_remote_order(
        &mut self,
        event: &RemoteOrderUpdate,
    ) -> Result<(), String> {
        let remote_order_id = crate::domain::RemoteOrderId::new(event.order_id.to_string())?;
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
                first_seen_at_unix_nanos: event.occurred_at_unix_nanos,
                last_seen_at_unix_nanos: event.occurred_at_unix_nanos,
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
        entry.last_seen_at_unix_nanos = event.occurred_at_unix_nanos;
        entry.reason = event.reason.clone();
        Ok(())
    }
}

fn order_event(order: &ExecutionOrder, occurred_at: u64, reason: String) -> ExecutionEvent {
    ExecutionEvent {
        order_id: order.order_id.clone(),
        intent_id: order.intent_id.clone(),
        plan_id: order.plan_id.clone(),
        leg_id: order.leg_id.clone(),
        status: order.status,
        remote_order_id: order.remote_order_id.clone(),
        occurred_at_unix_nanos: occurred_at.into(),
        reason,
        fill_id: None,
        filled_quantity: None,
    }
}
