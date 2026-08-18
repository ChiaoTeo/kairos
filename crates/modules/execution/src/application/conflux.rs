use std::collections::BTreeMap;
use std::time::Duration;

use kairos_conflux::{
    ConfluxActor, ConfluxEvent, Context, Contract, IntegrationEvent, RestContract, SystemEvent,
};
use kairos_execution_contract::{
    ExecutionCommandStatus, ExecutionControlError, ExecutionHealthResponse,
    ExecutionReconcileResponse, ExecutionRestRequest, ExecutionRestResponse,
    ExecutionRouteCandidateResponse, ExecutionRouteHealth, ExecutionRoutesResponse,
};
use kairos_integration::ExternalParticipantEvent;
use kairos_protocol::InstanceIdentity;
use kairos_transport::SnapshotEnvelopeMetadata;

use super::{
    CancelOrder, ExecuteStrategyIntent, ExecutionApplication, ExecutionError,
    ExecutionOrderOptions, ExecutionRouteQuery, RemoteOrderQuery, ReplaceOrder, SubmitOrder,
};
use crate::services::actor::RemoteOrderEvent;
use crate::services::audit::ExecutionAudit;
use crate::services::gateway::{
    build_managed_gateways, ExecutionConnectionPlan, ExecutionWriterFence,
};
use crate::services::persistence::ExecutionOutboxEvent;
use crate::services::simulation::SimulatedAccountSettlement;

pub struct ExecutionRest;

impl RestContract for ExecutionRest {
    type Request = ExecutionRestRequest;
    type Response = ExecutionRestResponse;
}

impl Contract for ExecutionApplication {
    type Rest = ExecutionRest;
}

pub(crate) struct ExecutionConfluxState {
    plans: Vec<ExecutionConnectionPlan>,
    writer_fences: Vec<ExecutionWriterFence>,
    gateway_shutdown: Option<tokio::sync::watch::Sender<bool>>,
    identity: InstanceIdentity,
    view_root: std::path::PathBuf,
    view_slot_size: usize,
    producer_incarnation: u64,
    route_status: BTreeMap<String, (bool, String)>,
    audit: Option<ExecutionAudit>,
    simulated_account_settlement: Option<SimulatedAccountSettlement>,
}

impl Default for ExecutionConfluxState {
    fn default() -> Self {
        Self {
            plans: Vec::new(),
            writer_fences: Vec::new(),
            gateway_shutdown: None,
            identity: InstanceIdentity::default(),
            view_root: std::path::PathBuf::new(),
            view_slot_size: 4 * 1024 * 1024,
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
            route_status: BTreeMap::new(),
            audit: None,
            simulated_account_settlement: None,
        }
    }
}

impl ExecutionApplication {
    pub(crate) fn configure_conflux(
        &mut self,
        plans: Vec<ExecutionConnectionPlan>,
        writer_fences: Vec<ExecutionWriterFence>,
        identity: InstanceIdentity,
        view_root: std::path::PathBuf,
        view_slot_size: usize,
        audit: ExecutionAudit,
        simulated_account_settlement: Option<SimulatedAccountSettlement>,
    ) -> Result<(), String> {
        if view_slot_size == 0 {
            return Err("Execution Conflux view slot size must be positive".into());
        }
        self.conflux.route_status = plans
            .iter()
            .map(|plan| (plan.route_id.clone(), (plan.required, "created".into())))
            .collect();
        self.conflux.plans = plans;
        self.conflux.writer_fences = writer_fences;
        self.conflux.identity = identity;
        self.conflux.view_root = view_root;
        self.conflux.view_slot_size = view_slot_size;
        self.conflux.audit = Some(audit);
        self.conflux.simulated_account_settlement = simulated_account_settlement;
        Ok(())
    }

    fn start_managed_connections(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), ExecutionError> {
        if !self.conflux.plans.is_empty() {
            let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
            let (entry, query, entry_task, query_task) = build_managed_gateways(
                context.system(),
                &self.conflux.plans,
                std::mem::take(&mut self.conflux.writer_fences),
                shutdown_rx,
            )
            .map_err(ExecutionError::Gateway)?;
            self.install_order_entry(Box::new(entry));
            self.install_order_query(Box::new(query));
            context.spawn_task(entry_task);
            context.spawn_task(query_task);
            self.conflux.gateway_shutdown = Some(shutdown);
        }
        for plan in self.conflux.plans.clone() {
            spawn_execution_stream(context, &plan)?;
        }
        Ok(())
    }

    async fn maintain(&mut self) -> Result<(), ExecutionError> {
        let now = now_unix_nanos();
        let business_now = self.business_time_unix_nanos().unwrap_or(now);
        if self.has_order_query() {
            match self.reconcile_remote_orders(RemoteOrderQuery {
                limit: Some(200),
                ..Default::default()
            }) {
                Ok(_) => self.complete_writer_reconciliation(),
                Err(error) => {
                    tracing::warn!(component = "execution", error = %error, "Execution reconciliation failed")
                }
            }
        }
        self.refresh_maker_quotes()?;
        self.advance_due_intent_orders(business_now, 64)?;
        self.expire_due_intents(business_now)?;
        Ok(())
    }
}

impl ConfluxActor for ExecutionApplication {
    type FatalError = ExecutionError;
    type LocalEvent = ();

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.start_managed_connections(context)?;
        context.spawn_timer("maintenance", Duration::from_secs(1));
        self.publish(context)?;
        Ok(())
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent<Self, Self::LocalEvent>,
        context: &mut Context<'_, Self>,
    ) -> Result<Option<ExecutionRestResponse>, Self::FatalError> {
        let response = match event {
            ConfluxEvent::Rest(request) => Some(self.handle_rest(request)),
            ConfluxEvent::Integration(IntegrationEvent {
                connection,
                event: ExternalParticipantEvent::Execution(event),
            }) => {
                let event = remote_order_event(event, connection);
                if self.accept_remote_event_identity(&event.event_id) {
                    if let Err(error) = self.apply_remote_execution_event(event.event) {
                        tracing::warn!(component = "execution", error = %error, "Execution rejected provider event");
                    }
                }
                None
            }
            ConfluxEvent::System(SystemEvent::SourceReady { source }) => {
                self.update_route_status(&source, "ready");
                None
            }
            ConfluxEvent::System(SystemEvent::SourceFailed { source, error }) => {
                self.update_route_status(&source, "degraded");
                tracing::warn!(component = "execution", %source, %error, "Execution source failed");
                None
            }
            ConfluxEvent::System(SystemEvent::Timer { name, .. }) if name == "maintenance" => {
                self.maintain().await?;
                None
            }
            _ => None,
        };
        self.publish(context)?;
        Ok(response)
    }

    async fn stopping(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        if let Some(shutdown) = self.conflux.gateway_shutdown.take() {
            let _ = shutdown.send(true);
        }
        self.publish(context)
    }
}

impl ExecutionApplication {
    fn handle_rest(&mut self, request: ExecutionRestRequest) -> ExecutionRestResponse {
        match request {
            ExecutionRestRequest::Health => {
                ExecutionRestResponse::Health(Ok(self.contract_health()))
            }
            ExecutionRestRequest::Routes(query) => {
                let participant = query.participant_id.clone();
                let parsed = parse_route_query(&query);
                ExecutionRestResponse::Routes(
                    parsed
                        .map(|query| {
                            let routes = self
                                .available_execution_routes(&query)
                                .into_iter()
                                .filter(|route| {
                                    participant.as_deref().is_none_or(|value| {
                                        route.participant_id.eq_ignore_ascii_case(value)
                                    })
                                })
                                .map(route_response)
                                .collect();
                            ExecutionRoutesResponse { routes }
                        })
                        .map_err(control_error),
                )
            }
            ExecutionRestRequest::SubmitIntent(request) => {
                let result = serde_json::from_value::<ExecuteStrategyIntent>(request.intent)
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))
                    .and_then(|intent| {
                        let key = request
                            .envelope
                            .idempotency_key
                            .or(request.envelope.command_id.clone())
                            .ok_or_else(|| {
                                ExecutionError::Invalid("idempotency_key is required".into())
                            })?;
                        self.submit_intent_with_idempotency(intent, key).map(
                            |(intent, duplicate)| ExecutionCommandStatus {
                                status: if duplicate { "duplicate" } else { "accepted" }.into(),
                                command_id: request.envelope.command_id,
                                intent_id: Some(intent.intent.intent_id.to_string()),
                                order_id: None,
                            },
                        )
                    });
                ExecutionRestResponse::SubmitIntent(result.map_err(control_error))
            }
            ExecutionRestRequest::CancelOrder { order_id, request } => {
                let result = kairos_primitives::OrderId::new(order_id)
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))
                    .and_then(|order_id| {
                        self.cancel(CancelOrder {
                            order_id,
                            reason: request.reason.unwrap_or_default(),
                        })
                    })
                    .map(|order| command_status("accepted", Some(order.order_id.to_string())));
                ExecutionRestResponse::CancelOrder(result.map_err(control_error))
            }
            ExecutionRestRequest::ReplaceOrder { order_id, request } => {
                let result = self.replace_contract_order(order_id, request);
                ExecutionRestResponse::ReplaceOrder(result.map_err(control_error))
            }
            ExecutionRestRequest::Reconcile(request) => {
                let query = request
                    .order_id
                    .map(kairos_primitives::OrderId::new)
                    .transpose()
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))
                    .map(|order_id| RemoteOrderQuery {
                        binding_id: request
                            .execution_route_id
                            .map(|route| format!("execution.{route}.query")),
                        order_id,
                        limit: Some(200),
                        ..Default::default()
                    });
                ExecutionRestResponse::Reconcile(
                    query
                        .and_then(|query| {
                            self.reconcile_remote_orders(query)
                                .map(|changed| ExecutionReconcileResponse { changed })
                        })
                        .map_err(control_error),
                )
            }
        }
    }

    fn replace_contract_order(
        &mut self,
        order_id: String,
        patch: kairos_execution_contract::ReplaceOrderRequest,
    ) -> Result<ExecutionCommandStatus, ExecutionError> {
        let order_id = kairos_primitives::OrderId::new(order_id)
            .map_err(|e| ExecutionError::Invalid(e.to_string()))?;
        let original = self
            .orders(None)
            .into_iter()
            .find(|order| order.order_id == order_id)
            .ok_or_else(|| ExecutionError::Invalid("order not found".into()))?;
        let options = patch
            .options
            .map(serde_json::from_value::<ExecutionOrderOptions>)
            .transpose()
            .map_err(|e| ExecutionError::Invalid(e.to_string()))?
            .unwrap_or_default();
        let replacement = SubmitOrder {
            order_id: kairos_primitives::OrderId::new(format!("{}:replacement", order_id))
                .map_err(|e| ExecutionError::Invalid(e.to_string()))?,
            intent_id: original.intent_id,
            strategy_id: original.strategy_id,
            account_id: original.account_id,
            segment_key: original.segment_key,
            instrument_id: original.instrument_id,
            market_id: original.market_id,
            execution_route_id: original.execution_route_id,
            side: original.side,
            order_type: original.order_type,
            quantity: patch
                .quantity
                .map(|v| v.parse())
                .transpose()
                .map_err(|e| ExecutionError::Invalid(format!("invalid quantity: {e}")))?
                .unwrap_or(original.quantity),
            limit_price: patch
                .limit_price
                .map(|v| v.parse())
                .transpose()
                .map_err(|e| ExecutionError::Invalid(format!("invalid limit price: {e}")))?
                .or(original.limit_price),
            options,
            submitted_at_unix_nanos: None,
        };
        self.replace(ReplaceOrder {
            order_id,
            replacement,
        })
        .map(|order| command_status("accepted", Some(order.order_id.to_string())))
    }

    fn contract_health(&self) -> ExecutionHealthResponse {
        let routes = self
            .conflux
            .route_status
            .iter()
            .map(|(route_id, (required, status))| ExecutionRouteHealth {
                route_id: route_id.clone(),
                status: status.clone(),
                required: *required,
            })
            .collect::<Vec<_>>();
        let routes_ready = routes
            .iter()
            .all(|route| !route.required || route.status == "ready");
        ExecutionHealthResponse {
            status: if routes_ready && self.writer_recovery_ready() {
                "ready"
            } else {
                "degraded"
            }
            .into(),
            writer_recovery_ready: self.writer_recovery_ready(),
            routes,
        }
    }

    fn update_route_status(&mut self, source: &str, status: &str) {
        for plan in &self.conflux.plans {
            if source.ends_with(&plan.stream_key) {
                if let Some(value) = self.conflux.route_status.get_mut(&plan.route_id) {
                    value.1 = status.into();
                }
            }
        }
    }

    fn publish(&mut self, context: &mut Context<'_, Self>) -> Result<(), ExecutionError> {
        self.flush_durable_events()?;
        let actor_id = self.snapshot().actor_id.to_string();
        while let Some(event) = self.pending_business_event().cloned() {
            let publisher = context
                .system()
                .aeron_publishers
                .get_mut(&"execution-events".to_owned())
                .ok_or_else(|| {
                    ExecutionError::Gateway("missing execution-events Aeron publisher".into())
                })?;
            for (index, change) in event.changes.iter().enumerate() {
                for bytes in crate::services::publication::encode_business_change(
                    &actor_id,
                    &self.conflux.identity,
                    event.sequence.get(),
                    event.occurred_at_unix_nanos.get(),
                    index,
                    change,
                )
                .map_err(ExecutionError::Gateway)?
                {
                    publisher
                        .resource_mut()
                        .publish(&bytes)
                        .map_err(|e| ExecutionError::Gateway(e.to_string()))?;
                }
            }
            self.acknowledge_business_event();
        }
        self.publish_views(context, &actor_id)
    }

    fn flush_durable_events(&mut self) -> Result<(), ExecutionError> {
        let durable = self.pending_outbox(1_024)?;
        let mut acknowledged = Vec::with_capacity(durable.len());
        let mut orders = Vec::new();
        let mut intents = Vec::new();
        for entry in &durable {
            match &entry.event {
                ExecutionOutboxEvent::Order(event) => {
                    if let Some(fill_id) = event.fill_id.as_ref() {
                        if self.conflux.simulated_account_settlement.is_some() {
                            let (fill, order, commitment) = self
                                .simulated_settlement_fact(fill_id)
                                .map_err(ExecutionError::Persistence)?;
                            self.conflux
                                .simulated_account_settlement
                                .as_mut()
                                .expect("settlement presence checked")
                                .apply_fill(&fill, &order, &commitment)
                                .map_err(ExecutionError::Gateway)?;
                        }
                    }
                    orders.push(event.clone());
                }
                ExecutionOutboxEvent::Intent(event) => intents.push(event.clone()),
            }
            acknowledged.push(entry.id);
        }
        if let Some(mut audit) = self.conflux.audit.take() {
            let order_events = self.drain_events();
            let intent_events = self.drain_intent_events();
            let result = audit
                .publish_batch(&orders, &intents)
                .and_then(|()| audit.publish_batch(&order_events, &intent_events));
            self.conflux.audit = Some(audit);
            result.map_err(ExecutionError::Persistence)?;
        }
        self.acknowledge_outbox(&acknowledged)
    }

    fn publish_views(
        &mut self,
        context: &mut Context<'_, Self>,
        actor_id: &str,
    ) -> Result<(), ExecutionError> {
        use kairos_execution_contract::{
            ExecutionViewKey, ExecutionViewKind, ExecutionViewPublisher,
        };
        let snapshot = self.current_view();
        let metadata = SnapshotEnvelopeMetadata {
            resource_epoch: 1,
            producer_incarnation: self.conflux.producer_incarnation,
            generation: snapshot.generation.get(),
            applied_event_sequence: snapshot.event_sequence.get(),
            published_at_unix_nanos: now_unix_nanos(),
        };
        for kind in [
            ExecutionViewKind::ActiveOrders,
            ExecutionViewKind::CurrentExecution,
            ExecutionViewKind::ActiveIntents,
        ] {
            let key = ExecutionViewKey::new(
                self.conflux.identity.workspace_id.clone(),
                kind.clone(),
                Some(self.conflux.identity.launch_id.clone()),
                Some(self.conflux.identity.instance_id.clone()),
            )
            .map_err(|e| ExecutionError::Gateway(e.to_string()))?;
            let bytes = match kind {
                ExecutionViewKind::ActiveOrders => {
                    crate::services::publication::encode_active_orders(
                        actor_id,
                        &self.conflux.identity,
                        snapshot.generation.get(),
                        &key,
                        &snapshot,
                    )
                }
                ExecutionViewKind::CurrentExecution => {
                    crate::services::publication::encode_current_execution(
                        actor_id,
                        &self.conflux.identity,
                        snapshot.generation.get(),
                        &key,
                        &snapshot,
                    )
                }
                ExecutionViewKind::ActiveIntents => {
                    crate::services::publication::encode_active_intents(
                        actor_id,
                        &self.conflux.identity,
                        snapshot.generation.get(),
                        &key,
                        &snapshot,
                    )
                }
            }
            .map_err(ExecutionError::Gateway)?;
            let resource_key = key.canonical_key();
            if context
                .system()
                .execution_view_publishers
                .get(&resource_key)
                .is_none()
            {
                let publisher = ExecutionViewPublisher::create(
                    &self.conflux.view_root,
                    key,
                    self.conflux.view_slot_size,
                )
                .map_err(|e| ExecutionError::Gateway(e.to_string()))?;
                context
                    .system()
                    .execution_view_publishers
                    .ensure_with(resource_key.clone(), 1, || publisher)
                    .map_err(|e| ExecutionError::Gateway(e.to_string()))?;
            }
            context
                .system()
                .execution_view_publishers
                .get_mut(&resource_key)
                .expect("publisher inserted")
                .resource_mut()
                .publish(metadata, &bytes)
                .map_err(|e| ExecutionError::Gateway(e.to_string()))?;
        }
        Ok(())
    }
}

fn spawn_execution_stream(
    context: &mut Context<'_, ExecutionApplication>,
    plan: &ExecutionConnectionPlan,
) -> Result<(), ExecutionError> {
    let key = plan.stream_key.clone();
    macro_rules! spawn {
        ($field:ident) => {
            if let Some(value) = context.system().$field.remove(&key) {
                context.spawn_integration_execution_events(key.clone(), value.into_connection());
                return Ok(());
            }
        };
    }
    spawn!(binance_spot_user_websocket_connections);
    spawn!(binance_margin_user_websocket_connections);
    spawn!(binance_usdm_user_websocket_connections);
    spawn!(binance_coinm_user_websocket_connections);
    spawn!(binance_options_user_websocket_connections);
    spawn!(binance_stocks_user_websocket_connections);
    spawn!(okx_private_websocket_connections);
    spawn!(ibkr_execution_stream_connections);
    Err(ExecutionError::Gateway(format!(
        "missing managed Execution stream: {key}"
    )))
}

fn remote_order_event(
    envelope: kairos_integration::ExternalEventEnvelope<kairos_integration::ExternalExecutionEvent>,
    connection: String,
) -> RemoteOrderEvent {
    let event_id = envelope.participant_event_id.clone().unwrap_or_else(|| {
        format!(
            "{}:{:?}:{}",
            envelope.payload.order_id,
            envelope.payload.status,
            envelope.payload.occurred_at_unix_nanos.get()
        )
    });
    let event = envelope.payload;
    RemoteOrderEvent {
        event_id,
        connection_id: if envelope.binding_id.is_empty() {
            connection
        } else {
            envelope.binding_id
        },
        event: super::RemoteOrderUpdate {
            order_id: event.order_id,
            symbol: event.symbol,
            status: super::remote_status(&format!("{:?}", event.status)),
            fill_quantity: event.fill_quantity.and_then(|v| decimal(v).parse().ok()),
            fill_price: event.fill_price.and_then(|v| decimal(v).parse().ok()),
            execution_id: event.execution_id,
            fee_currency: event.fee_currency,
            fee_amount: event.fee_amount.and_then(|v| decimal(v).parse().ok()),
            occurred_at_unix_nanos: event.occurred_at_unix_nanos,
            reason: event.reason,
        },
    }
}

fn decimal(value: kairos_integration::DecimalValue) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.unsigned_abs().to_string();
    let scale = usize::from(value.scale);
    let body = if digits.len() <= scale {
        format!("0.{}{}", "0".repeat(scale - digits.len()), digits)
    } else {
        let split = digits.len() - scale;
        format!("{}.{}", &digits[..split], &digits[split..])
    };
    if negative {
        format!("-{body}")
    } else {
        body
    }
}

fn parse_route_query(
    query: &kairos_execution_contract::ExecutionRoutesQuery,
) -> Result<ExecutionRouteQuery, ExecutionError> {
    Ok(ExecutionRouteQuery {
        account_id: query
            .account_id
            .as_deref()
            .map(kairos_primitives::AccountId::new)
            .transpose()
            .map_err(|e| ExecutionError::Invalid(e.to_string()))?,
        segment_key: query
            .segment_key
            .as_deref()
            .map(kairos_primitives::SegmentKey::new)
            .transpose()
            .map_err(|e| ExecutionError::Invalid(e.to_string()))?,
        instrument_id: query
            .instrument_id
            .as_deref()
            .map(kairos_primitives::InstrumentId::new)
            .transpose()
            .map_err(|e| ExecutionError::Invalid(e.to_string()))?,
        market_id: query
            .market_id
            .as_deref()
            .map(kairos_primitives::MarketId::new)
            .transpose()
            .map_err(|e| ExecutionError::Invalid(e.to_string()))?,
        ..Default::default()
    })
}

fn route_response(route: super::ExecutionRouteCandidate) -> ExecutionRouteCandidateResponse {
    ExecutionRouteCandidateResponse {
        route_id: route.route_id.to_string(),
        account_id: route.account_id.map(|v| v.to_string()),
        segment_key: route.segment_key.map(|v| v.to_string()),
        instrument_id: route.instrument_id.map(|v| v.to_string()),
        market_id: route.market_id.map(|v| v.to_string()),
        participant_id: route.participant_id,
        provider_product: route.provider_product.to_string(),
        provider_symbol: route.provider_symbol.to_string(),
        supported_order_types: route
            .supported_order_types
            .into_iter()
            .map(|v| format!("{v:?}").to_ascii_lowercase())
            .collect(),
        supported_options: route.supported_options,
        ready: route.ready,
    }
}

fn command_status(status: &str, order_id: Option<String>) -> ExecutionCommandStatus {
    ExecutionCommandStatus {
        status: status.into(),
        command_id: None,
        intent_id: None,
        order_id,
    }
}
fn control_error(error: impl ToString) -> ExecutionControlError {
    ExecutionControlError {
        code: "execution.request_failed".into(),
        message: error.to_string(),
    }
}
fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}
