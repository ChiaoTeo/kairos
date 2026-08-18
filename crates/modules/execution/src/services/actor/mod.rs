//! Runtime input facts for the Execution state owner.
//!
//! Commands and exchange facts are deliberately different inputs. Commands are
//! requests from callers and have a reply path; exchange events are facts from a
//! private order stream and are consumed independently by the state owner.

use std::collections::{BTreeMap, HashSet, VecDeque};

use crate::application::{
    ExecutionEvent, ExecutionFillReport, IntentEvent, IntentState, SubmitOrder, UnknownRemoteOrder,
    UnknownRemoteOrderResolution,
};
use crate::domain::{
    CommitmentBasis, CommitmentStatus, ExecutionFill, ExecutionOrder, ExecutionOrderStatus,
    LegLifecycle, Money, OrderCommitment, OrderId, Quantity, RiskReservationEvidence,
    RiskReservationSagaStatus, UnixNanos,
};
use kairos_conflux::OrderEntryEvent;

mod events;
mod fills;
mod intents;
mod orders;
mod reconciliation;
mod transitions;

pub use events::RemoteOrderEvent;
pub(crate) use transitions::FillTransition;

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
        attempt: order.attempts.last().cloned(),
    }
}
