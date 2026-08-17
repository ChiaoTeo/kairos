use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::{
    ActorId, Currency, DurationNanos, ExecutionAccessId, FillId, IntentId, Money, OrderId, Price,
    Quantity, StrategyId, UnixNanos,
};

use super::model::*;
use super::RemoteOrderUpdate;
use crate::domain::{
    split_quantity, CommitmentBasis, CommitmentResource, CommitmentStatus, CompletionPolicy,
    ExecutionFill, ExecutionLeg, ExecutionOrder, ExecutionOrderStatus, ExecutionPlan,
    FailurePolicy, HedgePolicy, IntentType, MakerExecutionPolicy, OrderCommitment, OrderSide,
    OrderType, RiskReservationEvidence, RiskReservationSagaStatus, SplitOrderPolicy,
};

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

mod intent_use_cases;
mod order_use_cases;

pub struct ExecutionApplication {
    actor_id: String,
    actor: crate::services::actor::ExecutionActor,
    pending_business_events: std::collections::VecDeque<ExecutionBusinessEvent>,
    execution_accesses:
        BTreeMap<ExecutionAccessId, kairos_integration::application::ProviderInstrumentRef>,
    order_entry: Option<Box<dyn OrderEntryConnection>>,
    order_query: Option<Box<dyn OrderQueryConnection>>,
    execution_stream: Option<Box<dyn OrderEventSource>>,
    store: Option<Box<dyn ExecutionStateStore>>,
    intent_planner: Option<Box<dyn super::ExecutionIntentPlanner>>,
    order_admission: Option<Box<dyn super::ExecutionOrderAdmission>>,
    risk_reservations: Option<Box<dyn super::ExecutionRiskReservations>>,
    account_facts: Option<Box<dyn super::ExecutionAccountFacts>>,
    live_trading: bool,
    live_confirmed: bool,
    writer_recovery_ready: bool,
    risk_recovery_ready: bool,
    risk_recovery_error: Option<String>,
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
            actor: crate::services::actor::ExecutionActor::new(),
            pending_business_events: std::collections::VecDeque::new(),
            execution_accesses: BTreeMap::new(),
            order_entry,
            order_query,
            execution_stream,
            store,
            intent_planner: None,
            order_admission: None,
            risk_reservations: None,
            account_facts: None,
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

    /// Install Reference-owned provider addresses for explicit execution
    /// access IDs. The application never reconstructs these addresses from a
    /// canonical MarketId or provider symbol.
    pub fn configure_execution_access(
        &mut self,
        access_id: ExecutionAccessId,
        provider_instrument: kairos_integration::application::ProviderInstrumentRef,
    ) {
        self.execution_accesses
            .insert(access_id, provider_instrument);
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
        if query.since_unix_nanos.is_none() && self.actor.remote_watermark() > 0 {
            query.since_unix_nanos = Some(
                self.actor
                    .remote_watermark()
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
                .actor
                .order_map()
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
                            fee_currency: None,
                            occurred_at_unix_nanos: remote_order.occurred_at_unix_nanos,
                            execution_market_id: local.market_id.clone(),
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

            let local = self
                .actor
                .order_map()
                .get(&local.order_id)
                .cloned()
                .ok_or_else(|| ExecutionError::Invalid("reconciled order disappeared".into()))?;
            if local.status != reconciled_status
                || local.remote_order_id.as_deref() != Some(remote_order.order_id.as_str())
            {
                let occurred_at = remote_order
                    .occurred_at_unix_nanos
                    .unwrap_or_else(|| now_nanos().into())
                    .get();
                let (_, event) = self
                    .actor
                    .reconcile_order(
                        local.order_id.as_str(),
                        &remote_order.order_id,
                        reconciled_status,
                        occurred_at,
                        reconciliation_reason,
                    )
                    .map_err(ExecutionError::Invalid)?;
                let reconciled = self
                    .actor
                    .order(local.order_id.as_str())
                    .cloned()
                    .ok_or_else(|| {
                        ExecutionError::Invalid("reconciled order disappeared".into())
                    })?;
                self.update_commitment_from_order(&reconciled, occurred_at)?;
                let risk_effect = match reconciled.status {
                    ExecutionOrderStatus::Filled => Some(RiskReservationSagaStatus::ConsumePending),
                    status if status.terminal() => Some(RiskReservationSagaStatus::ReleasePending),
                    _ => None,
                };
                if let Some(status) = risk_effect {
                    self.actor.set_risk_reservation_status(
                        reconciled.order_id.as_str(),
                        status,
                        occurred_at,
                    );
                }
                self.commit(event)?;
                match risk_effect {
                    Some(RiskReservationSagaStatus::ConsumePending) => {
                        self.complete_risk_consume(reconciled.order_id.as_str(), occurred_at)?
                    }
                    Some(RiskReservationSagaStatus::ReleasePending) => {
                        self.complete_risk_release(reconciled.order_id.as_str(), occurred_at)?
                    }
                    _ => {}
                }
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
        self.actor
            .observe_remote_time(event.occurred_at_unix_nanos.get());
        let local = self
            .actor
            .find_remote_order(event.order_id.as_str())
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
                fee_currency: event.fee_currency.clone(),
                occurred_at_unix_nanos: Some(event.occurred_at_unix_nanos),
                execution_market_id: local.market_id.clone(),
            })?;
            return Ok(fill);
        }
        let occurred_at = event.occurred_at_unix_nanos.get();
        let (next, persisted_event) = self
            .actor
            .reconcile_order(
                local.order_id.as_str(),
                event.order_id.as_str(),
                event.status,
                occurred_at,
                event.reason,
            )
            .map_err(ExecutionError::Invalid)?;
        self.update_commitment_from_order(&next, occurred_at)?;
        let risk_effect = match next.status {
            ExecutionOrderStatus::Filled => Some(RiskReservationSagaStatus::ConsumePending),
            status if status.terminal() => Some(RiskReservationSagaStatus::ReleasePending),
            _ => None,
        };
        if let Some(status) = risk_effect {
            self.actor
                .set_risk_reservation_status(next.order_id.as_str(), status, occurred_at);
        }
        self.commit(persisted_event)?;
        match risk_effect {
            Some(RiskReservationSagaStatus::ConsumePending) => {
                self.complete_risk_consume(next.order_id.as_str(), occurred_at)?
            }
            Some(RiskReservationSagaStatus::ReleasePending) => {
                self.complete_risk_release(next.order_id.as_str(), occurred_at)?
            }
            _ => {}
        }
        info!(event = "remote_execution_event_reconciled", component = "execution", order_id = %next.order_id, status = ?next.status, "remote execution event reconciled");
        Ok(next)
    }

    pub(crate) fn accept_remote_event_identity(&mut self, event_id: &str) -> bool {
        self.actor.accept_exchange_event(event_id)
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

    pub fn unknown_remote_orders(&self) -> Vec<UnknownRemoteOrder> {
        self.actor.unknown_remote_orders().cloned().collect()
    }

    /// Mark a previously unknown remote order as resolved once an operator or
    /// reconciliation process has established its local order association.
    pub fn resolve_unknown_remote_order(
        &mut self,
        remote_order_id: &str,
        resolution: UnknownRemoteOrderResolution,
        reason: impl Into<String>,
    ) -> Result<(), ExecutionError> {
        self.actor
            .resolve_unknown_remote_order(remote_order_id, resolution, reason.into(), now_nanos())
            .map_err(ExecutionError::Invalid)?;
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
        let (unknown, local, event) = self
            .actor
            .link_unknown_remote_order(remote_order_id, local_order_id)
            .map_err(ExecutionError::Invalid)?;
        self.commit(event)?;
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
                fee_currency: unknown.fee_currency,
                occurred_at_unix_nanos: Some(unknown.last_seen_at_unix_nanos),
                execution_market_id: local.market_id.clone(),
            });
        }
        self.persist_snapshot()?;
        Ok(local)
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

    pub fn configure_live_trading(&mut self, enabled: bool, confirmed: bool) {
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

    pub fn attach_intent_planner(&mut self, planner: Box<dyn super::ExecutionIntentPlanner>) {
        self.intent_planner = Some(planner);
    }

    pub fn attach_order_admission(&mut self, admission: Box<dyn super::ExecutionOrderAdmission>) {
        self.order_admission = Some(admission);
    }

    pub fn attach_risk_reservations(
        &mut self,
        risk_reservations: Box<dyn super::ExecutionRiskReservations>,
    ) {
        self.risk_reservations = Some(risk_reservations);
    }

    pub fn attach_account_facts(&mut self, account_facts: Box<dyn super::ExecutionAccountFacts>) {
        self.account_facts = Some(account_facts);
    }

    /// Resolve every durable in-flight Risk saga exclusively from Risk's
    /// typed mmap current view. A missing or stale observation keeps the live
    /// admission barrier closed; recovery never retries an uncertain money or
    /// capacity command merely because Execution restarted.
    pub fn recover_risk_reservations(&mut self) -> Result<(), ExecutionError> {
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
    execution_accesses: &BTreeMap<
        ExecutionAccessId,
        kairos_integration::application::ProviderInstrumentRef,
    >,
) -> Result<OrderEntryRequest, String> {
    let access_id = order.execution_access_id.as_ref().ok_or_else(|| {
        "execution_access_id is required; provider identity is not inferred".to_string()
    })?;
    let provider_instrument = execution_accesses
        .get(access_id)
        .cloned()
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
