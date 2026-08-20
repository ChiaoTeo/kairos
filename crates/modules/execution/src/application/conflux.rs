use std::collections::BTreeMap;
use std::time::Duration;

use kairos_conflux::{
    CommandOutcome, ConfluxActor, ConfluxEvent, ConnectionKey, Context, Contract,
    ExternalParticipantEvent, IntegrationError, IntegrationEvent, OrderCommand, OrderEntryEvent,
    OrderEntryRequest, RestContract, SnapshotEnvelopeMetadata, SystemEvent,
};
use kairos_execution_contract::{
    CompletionPolicy as ContractCompletionPolicy, ExecutionCommandStatus, ExecutionControlError,
    ExecutionHealthResponse, ExecutionIntentRequest, ExecutionOrderOptionsRequest,
    ExecutionReconcileResponse, ExecutionRestRequest, ExecutionRestResponse,
    ExecutionRouteCandidateResponse, ExecutionRouteHealth, ExecutionRoutesResponse,
    FailurePolicy as ContractFailurePolicy, HedgePolicyRequest, IntentAdmissionEvidenceRequest,
    IntentLegRequest as ContractIntentLegRequest, IntentType as ContractIntentType,
};
use kairos_primitives::runtime::InstanceIdentity;
use sha2::{Digest, Sha256};

use super::{
    CancelOrder, ExecuteStrategyIntent, ExecutionApplication, ExecutionError,
    ExecutionOrderOptions, ExecutionRouteQuery, IntentAdmissionEvidence, RemoteOrderQuery,
    SubmitOrder,
};
use crate::services::actor::RemoteOrderEvent;
use crate::services::audit::{ExecutionAudit, IntentAdmissionAuditRecord};
use crate::services::gateway::{ExecutionConnectionPlan, ExecutionWriterFence};
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
    identity: InstanceIdentity,
    producer_incarnation: u64,
    route_status: BTreeMap<String, (bool, String)>,
    audit: Option<ExecutionAudit>,
    pending_admissions: Vec<IntentAdmissionAuditRecord>,
    simulated_account_settlement: Option<SimulatedAccountSettlement>,
}

impl Default for ExecutionConfluxState {
    fn default() -> Self {
        Self {
            plans: Vec::new(),
            writer_fences: Vec::new(),
            identity: InstanceIdentity::default(),
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
            route_status: BTreeMap::new(),
            audit: None,
            pending_admissions: Vec::new(),
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
        audit: ExecutionAudit,
        simulated_account_settlement: Option<SimulatedAccountSettlement>,
    ) -> Result<(), String> {
        self.conflux.route_status = plans
            .iter()
            .map(|plan| (plan.route_id.clone(), (plan.required, "created".into())))
            .collect();
        self.conflux.plans = plans;
        self.conflux.writer_fences = writer_fences;
        self.conflux.identity = identity;
        self.conflux.audit = Some(audit);
        self.conflux.simulated_account_settlement = simulated_account_settlement;
        Ok(())
    }

    fn register_managed_streams(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), ExecutionError> {
        for plan in self.conflux.plans.clone() {
            register_execution_stream(context, &plan)?;
        }
        Ok(())
    }

    fn validate_writer_fence(&self, request: &OrderEntryRequest) -> Result<(), ExecutionError> {
        if self.conflux.writer_fences.is_empty() {
            return Ok(());
        }
        self.conflux
            .writer_fences
            .iter()
            .find(|fence| fence.validates(request))
            .ok_or_else(|| {
                ExecutionError::Gateway(format!(
                    "no Execution writer fence for account={}, segment={}",
                    request.account_id, request.segment_key
                ))
            })?
            .validate()
            .map_err(|error| ExecutionError::Gateway(error.to_string()))
    }

    async fn maintain(&mut self, context: &mut Context<'_, Self>) -> Result<(), ExecutionError> {
        let now = now_unix_nanos();
        let business_now = self.business_time_unix_nanos().unwrap_or(now);
        if !self.conflux.plans.is_empty() {
            match self
                .reconcile_managed_orders(
                    RemoteOrderQuery {
                        limit: Some(200),
                        ..Default::default()
                    },
                    context,
                )
                .await
            {
                Ok(_) => self.complete_writer_reconciliation(),
                Err(error) => {
                    tracing::warn!(component = "execution", error = %error, "Execution reconciliation failed")
                },
            }
        }
        self.refresh_maker_quotes_managed(context).await?;
        self.advance_due_intent_orders_managed(business_now, 64, context)
            .await?;
        self.expire_due_intents_managed(business_now, context)
            .await?;
        Ok(())
    }

    async fn advance_due_intent_orders_managed(
        &mut self,
        now_unix_nanos: u64,
        limit: usize,
        context: &mut Context<'_, Self>,
    ) -> Result<usize, ExecutionError> {
        let mut submitted = 0;
        while submitted < limit {
            let Some(due) = self.take_due_intent_order(now_unix_nanos)? else {
                break;
            };
            match self
                .submit_managed_order(due.request.clone(), context)
                .await
            {
                Ok(order) => {
                    self.complete_due_intent_order(&due, &order, now_unix_nanos)?;
                    submitted += 1;
                },
                Err(error) => {
                    let cancellations = self.fail_due_intent_order(&due, &error, now_unix_nanos)?;
                    for cancellation in cancellations {
                        if let Err(cancel_error) =
                            self.cancel_managed_order(cancellation, context).await
                        {
                            tracing::warn!(component = "execution", error = %cancel_error, "failed to cancel sibling after scheduled order failure");
                        }
                    }
                    return Err(error);
                },
            }
        }
        if submitted > 0 {
            self.persist_snapshot()?;
        }
        Ok(submitted)
    }

    async fn expire_due_intents_managed(
        &mut self,
        now_unix_nanos: u64,
        context: &mut Context<'_, Self>,
    ) -> Result<usize, ExecutionError> {
        let due = self.due_intent_expirations(now_unix_nanos);
        let count = due.len();
        for request in due {
            let cancellations = self.begin_intent_expiration(&request)?;
            for cancellation in cancellations {
                if let Err(error) = self.cancel_managed_order(cancellation, context).await {
                    tracing::warn!(component = "execution", error = %error, "failed to cancel child of expired intent");
                }
            }
            self.complete_intent_expiration(&request)?;
        }
        Ok(count)
    }

    async fn refresh_maker_quotes_managed(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<usize, ExecutionError> {
        let requests = self.maker_quote_refresh_requests()?;
        let mut refreshed = 0;
        'refresh: for request in requests {
            let prepared = match self.prepare_quote_refresh(request) {
                Ok(prepared) => prepared,
                Err(error) => {
                    tracing::warn!(component = "execution", error = %error, "maker quote refresh was rejected by execution guardrails");
                    continue;
                },
            };
            for cancellation in prepared.cancellations.clone() {
                if let Err(error) = self.cancel_managed_order(cancellation, context).await {
                    self.fail_prepared_quote_refresh(&prepared, &error)?;
                    tracing::warn!(component = "execution", error = %error, "maker quote refresh cancellation failed");
                    continue 'refresh;
                }
            }
            let mut orders = Vec::with_capacity(prepared.submissions.len());
            for (leg_id, submission) in prepared.submissions.clone() {
                match self.submit_managed_order(submission, context).await {
                    Ok(order) => orders.push((leg_id, order)),
                    Err(error) => {
                        self.fail_prepared_quote_refresh(&prepared, &error)?;
                        tracing::warn!(component = "execution", error = %error, "maker quote refresh submission failed");
                        continue 'refresh;
                    },
                }
            }
            self.complete_prepared_quote_refresh(prepared, orders)?;
            refreshed += 1;
        }
        Ok(refreshed)
    }

    async fn submit_compensating_hedge_managed(
        &mut self,
        intent_id: &str,
        context: &mut Context<'_, Self>,
    ) -> Result<(), ExecutionError> {
        let Some(prepared) = self.prepare_compensating_hedge(intent_id)? else {
            return Ok(());
        };
        match self
            .submit_managed_order(prepared.request.clone(), context)
            .await
        {
            Ok(order) => self.complete_compensating_hedge(&prepared, &order),
            Err(error) => self.fail_compensating_hedge(&prepared, &error),
        }
    }

    async fn submit_managed_order(
        &mut self,
        request: SubmitOrder,
        context: &mut Context<'_, Self>,
    ) -> Result<crate::ExecutionOrder, ExecutionError> {
        let (order, provider_request) = self.prepare_submission(request)?;
        self.validate_writer_fence(&provider_request)?;
        let plan = self
            .conflux
            .plans
            .iter()
            .find(|plan| {
                plan.account_id == provider_request.account_id
                    && plan.segment_key == provider_request.segment_key
                    && provider_request
                        .participant_instrument
                        .instrument_type
                        .as_ref()
                        .is_none_or(|kind| kind == &plan.instrument_type)
            })
            .ok_or_else(|| {
                ExecutionError::Gateway(format!(
                    "no managed execution route for account={}, segment={}",
                    provider_request.account_id, provider_request.segment_key
                ))
            })?;
        let key = ConnectionKey::new(plan.entry_key.clone()).map_err(ExecutionError::Gateway)?;
        self.begin_order_dispatch(order.order_id.as_str())?;
        let outcome = managed_submit_order(context, &key, &provider_request).await;
        self.complete_order_submission(order, outcome)
    }

    async fn cancel_managed_order(
        &mut self,
        request: CancelOrder,
        context: &mut Context<'_, Self>,
    ) -> Result<crate::ExecutionOrder, ExecutionError> {
        let prepared = self.prepare_cancellation(request)?;
        self.validate_writer_fence(&prepared.provider_request)?;
        let plan = self
            .conflux
            .plans
            .iter()
            .find(|plan| {
                plan.account_id == prepared.provider_request.account_id
                    && plan.segment_key == prepared.provider_request.segment_key
                    && prepared
                        .provider_request
                        .participant_instrument
                        .instrument_type
                        .as_ref()
                        .is_none_or(|kind| kind == &plan.instrument_type)
            })
            .ok_or_else(|| {
                ExecutionError::Gateway(format!(
                    "no managed execution route for account={}, segment={}",
                    prepared.provider_request.account_id, prepared.provider_request.segment_key
                ))
            })?;
        let key = ConnectionKey::new(plan.entry_key.clone()).map_err(ExecutionError::Gateway)?;
        let outcome = managed_cancel_order(
            context,
            &key,
            &prepared.provider_request,
            &prepared.remote_order_id,
            prepared.at_unix_nanos,
        )
        .await;
        self.complete_cancellation(prepared, outcome)
    }

    async fn reconcile_managed_orders(
        &mut self,
        query: RemoteOrderQuery,
        context: &mut Context<'_, Self>,
    ) -> Result<usize, ExecutionError> {
        let request = kairos_conflux::ExternalOrderQuery {
            instrument_type: None,
            symbol: query.symbol,
            order_id: query.order_id,
            limit: query.limit,
            since_unix_nanos: query.since_unix_nanos,
        };
        let keys = self
            .conflux
            .plans
            .iter()
            .filter(|plan| {
                query.binding_id.as_ref().is_none_or(|binding| {
                    plan.query_key == *binding
                        || plan.route_id == *binding
                        || binding.contains(&plan.route_id)
                })
            })
            .map(|plan| plan.query_key.clone())
            .collect::<std::collections::BTreeSet<_>>();
        let mut orders = Vec::new();
        for value in keys {
            let key = ConnectionKey::new(value).map_err(ExecutionError::Gateway)?;
            orders.extend(
                managed_open_orders(context, &key, &request)
                    .await
                    .map_err(|error| ExecutionError::Gateway(error.to_string()))?,
            );
            orders.extend(
                managed_order_history(context, &key, &request)
                    .await
                    .map_err(|error| ExecutionError::Gateway(error.to_string()))?,
            );
        }
        self.reconcile_external_orders(orders)
    }
}

async fn managed_submit_order(
    context: &mut Context<'_, ExecutionApplication>,
    key: &ConnectionKey,
    request: &OrderEntryRequest,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    macro_rules! try_family {
        ($field:ident) => {{
            let mut connections = context.connections();
            if let Ok(connection) = connections.$field.get(key) {
                return connection.submit_order(request).await;
            }
        }};
    }
    try_family!(binance_spot_rest);
    try_family!(binance_margin_rest);
    try_family!(binance_usdm_rest);
    try_family!(binance_coinm_rest);
    try_family!(binance_options_rest);
    try_family!(binance_stocks_rest);
    try_family!(okx_private_rest);
    try_family!(ibkr_order);
    Err(IntegrationError::Unavailable(format!(
        "managed Execution order connection is missing: {key}"
    )))
}

async fn managed_cancel_order(
    context: &mut Context<'_, ExecutionApplication>,
    key: &ConnectionKey,
    request: &OrderEntryRequest,
    remote_order_id: &str,
    at_unix_nanos: u64,
) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
    macro_rules! try_family {
        ($field:ident) => {{
            let mut connections = context.connections();
            if let Ok(connection) = connections.$field.get(key) {
                return connection
                    .cancel_order(request, remote_order_id, at_unix_nanos)
                    .await;
            }
        }};
    }
    try_family!(binance_spot_rest);
    try_family!(binance_margin_rest);
    try_family!(binance_usdm_rest);
    try_family!(binance_coinm_rest);
    try_family!(binance_options_rest);
    try_family!(binance_stocks_rest);
    try_family!(okx_private_rest);
    try_family!(ibkr_order);
    Err(IntegrationError::Unavailable(format!(
        "managed Execution order connection is missing: {key}"
    )))
}

async fn managed_open_orders(
    context: &mut Context<'_, ExecutionApplication>,
    key: &ConnectionKey,
    query: &kairos_conflux::ExternalOrderQuery,
) -> Result<Vec<kairos_conflux::ExternalOrder>, IntegrationError> {
    macro_rules! try_family {
        ($field:ident) => {{
            let mut connections = context.connections();
            if let Ok(connection) = connections.$field.get(key) {
                return kairos_conflux::OrderQuery::open_orders(connection, query).await;
            }
        }};
    }
    try_family!(binance_spot_rest);
    try_family!(binance_margin_rest);
    try_family!(binance_usdm_rest);
    try_family!(binance_coinm_rest);
    try_family!(binance_options_rest);
    try_family!(binance_stocks_rest);
    try_family!(okx_private_rest);
    try_family!(ibkr_order);
    Err(IntegrationError::Unavailable(format!(
        "managed Execution query connection is missing: {key}"
    )))
}

async fn managed_order_history(
    context: &mut Context<'_, ExecutionApplication>,
    key: &ConnectionKey,
    query: &kairos_conflux::ExternalOrderQuery,
) -> Result<Vec<kairos_conflux::ExternalOrder>, IntegrationError> {
    macro_rules! try_family {
        ($field:ident) => {{
            let mut connections = context.connections();
            if let Ok(connection) = connections.$field.get(key) {
                return kairos_conflux::OrderQuery::order_history(connection, query).await;
            }
        }};
    }
    try_family!(binance_spot_rest);
    try_family!(binance_margin_rest);
    try_family!(binance_usdm_rest);
    try_family!(binance_coinm_rest);
    try_family!(binance_options_rest);
    try_family!(binance_stocks_rest);
    try_family!(okx_private_rest);
    try_family!(ibkr_order);
    Err(IntegrationError::Unavailable(format!(
        "managed Execution query connection is missing: {key}"
    )))
}

impl ConfluxActor for ExecutionApplication {
    type FatalError = ExecutionError;
    type LocalEvent = ();

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.register_managed_streams(context)?;
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
            ConfluxEvent::Rest(request) => Some(self.handle_rest(request, context).await),
            ConfluxEvent::Integration(IntegrationEvent {
                identity,
                event: ExternalParticipantEvent::Execution(event),
            }) => {
                debug_assert_eq!(event.connection_key, identity.descriptor.connection_key);
                let event = remote_order_event(event);
                if self.accept_remote_event_identity(&event.event_id) {
                    match self.apply_remote_execution_event_deferred(event.event) {
                        Ok(order) => {
                            if let Some(intent_id) = order.intent_id.as_deref() {
                                self.submit_compensating_hedge_managed(intent_id, context)
                                    .await?;
                            }
                        },
                        Err(error) => {
                            tracing::warn!(component = "execution", error = %error, "Execution rejected provider event")
                        },
                    }
                }
                None
            },
            ConfluxEvent::System(SystemEvent::SourceReady { source }) => {
                self.update_route_status(&source, "ready");
                None
            },
            ConfluxEvent::System(SystemEvent::SourceFailed { source, error }) => {
                self.update_route_status(&source, "degraded");
                tracing::warn!(component = "execution", %source, %error, "Execution source failed");
                None
            },
            ConfluxEvent::System(SystemEvent::Timer { name, .. }) if name == "maintenance" => {
                self.maintain(context).await?;
                None
            },
            _ => None,
        };
        self.publish(context)?;
        Ok(response)
    }

    async fn stopping(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.publish(context)
    }
}

impl ExecutionApplication {
    async fn handle_rest(
        &mut self,
        request: ExecutionRestRequest,
        context: &mut Context<'_, Self>,
    ) -> ExecutionRestResponse {
        match request {
            ExecutionRestRequest::Health => {
                ExecutionRestResponse::Health(Ok(self.contract_health()))
            },
            ExecutionRestRequest::Routes(query) => {
                let participant = query.participant_id.clone();
                let parsed = parse_route_query(&query);
                ExecutionRestResponse::Routes(
                    parsed
                        .and_then(|query| {
                            let routes = self
                                .available_execution_routes(&query)
                                .into_iter()
                                .filter(|route| {
                                    participant.as_ref().is_none_or(|value| {
                                        route.participant_id.eq_ignore_ascii_case(value.as_str())
                                    })
                                })
                                .map(route_response)
                                .collect::<Result<Vec<_>, _>>()?;
                            Ok(ExecutionRoutesResponse { routes })
                        })
                        .map_err(control_error),
                )
            },
            ExecutionRestRequest::SubmitIntent(request) => {
                let command_id = request.envelope.command_id;
                let idempotency_key = request
                    .envelope
                    .idempotency_key
                    .or_else(|| {
                        command_id.as_ref().map(|value| {
                            kairos_primitives::runtime::IdempotencyKey::new(value.to_string())
                                .expect("request id is a valid idempotency fallback")
                        })
                    })
                    .ok_or_else(|| ExecutionError::Invalid("idempotency_key is required".into()));
                let decoded = decode_contract_intent(request.intent);
                let prepared = decoded.and_then(|intent| {
                    let evidence =
                        decode_intent_admission_evidence(request.admission_evidence, &intent)?;
                    Ok((intent, evidence))
                });
                let mut admission_result = "rejected".to_owned();
                let mut admission: Option<(IntentAdmissionEvidence, String, String)> = None;
                let result = match (prepared, idempotency_key) {
                    (Ok((intent, evidence)), Ok(key)) => {
                        if let Some(evidence) = evidence {
                            admission =
                                Some((evidence, key.to_string(), intent.intent_id.to_string()));
                        }
                        match self.accept_intent_with_idempotency_deferred(intent, key.to_string())
                        {
                            Ok((intent, duplicate)) => {
                                admission_result =
                                    if duplicate { "duplicate" } else { "accepted" }.into();
                                if !duplicate {
                                    let business_now = self
                                        .business_time_unix_nanos()
                                        .unwrap_or_else(now_unix_nanos);
                                    if let Err(error) = self
                                        .advance_due_intent_orders_managed(
                                            business_now,
                                            usize::MAX,
                                            context,
                                        )
                                        .await
                                    {
                                        Err(error)
                                    } else {
                                        Ok(ExecutionCommandStatus {
                                            status: "accepted".into(),
                                            command_id: command_id.clone(),
                                            intent_id: Some(intent.intent.intent_id.clone()),
                                            order_id: None,
                                        })
                                    }
                                } else {
                                    Ok(ExecutionCommandStatus {
                                        status: "duplicate".into(),
                                        command_id: command_id.clone(),
                                        intent_id: Some(intent.intent.intent_id.clone()),
                                        order_id: None,
                                    })
                                }
                            },
                            Err(error) => Err(error),
                        }
                    },
                    (Err(error), _) | (_, Err(error)) => Err(error),
                };
                if let Some((evidence, idempotency_key, intent_id)) = admission {
                    self.conflux
                        .pending_admissions
                        .push(IntentAdmissionAuditRecord {
                            command_id: command_id.map(|value| value.to_string()),
                            idempotency_key,
                            intent_id,
                            evidence,
                            admission_result,
                            created_at_unix_nanos: now_unix_nanos(),
                        });
                }
                ExecutionRestResponse::SubmitIntent(result.map_err(control_error))
            },
            ExecutionRestRequest::CancelOrder { order_id, request } => {
                let result = self
                    .cancel_managed_order(
                        CancelOrder {
                            order_id,
                            reason: request.reason.unwrap_or_default(),
                        },
                        context,
                    )
                    .await
                    .map(|order| command_status("accepted", Some(order.order_id.to_string())));
                ExecutionRestResponse::CancelOrder(result.map_err(control_error))
            },
            ExecutionRestRequest::ReplaceOrder { order_id, request } => {
                let result = self
                    .replace_contract_order(order_id, request, context)
                    .await;
                ExecutionRestResponse::ReplaceOrder(result.map_err(control_error))
            },
            ExecutionRestRequest::Reconcile(request) => {
                let query = Ok(RemoteOrderQuery {
                    binding_id: request
                        .execution_route_id
                        .map(|route| format!("execution.{route}.query")),
                    order_id: request.order_id,
                    limit: Some(200),
                    ..Default::default()
                });
                ExecutionRestResponse::Reconcile(
                    match query {
                        Ok(query) => {
                            self.reconcile_managed_orders(query, context)
                                .await
                                .map(|changed| ExecutionReconcileResponse {
                                    changed: changed as u64,
                                })
                        },
                        Err(error) => Err(error),
                    }
                    .map_err(control_error),
                )
            },
        }
    }

    async fn replace_contract_order(
        &mut self,
        order_id: kairos_primitives::execution::OrderId,
        patch: kairos_execution_contract::ReplaceOrderRequest,
        context: &mut Context<'_, Self>,
    ) -> Result<ExecutionCommandStatus, ExecutionError> {
        let original = self
            .orders(None)
            .into_iter()
            .find(|order| order.order_id == order_id)
            .ok_or_else(|| ExecutionError::Invalid("order not found".into()))?;
        let options = patch
            .options
            .map(decode_contract_options)
            .unwrap_or_default();
        let replacement = SubmitOrder {
            order_id: kairos_primitives::execution::OrderId::new(format!(
                "{}:replacement",
                order_id
            ))
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
            quantity: patch.quantity.unwrap_or(original.quantity),
            limit_price: patch.limit_price.or(original.limit_price),
            options,
            submitted_at_unix_nanos: None,
        };
        if !original.status.terminal() {
            self.cancel_managed_order(
                CancelOrder {
                    order_id,
                    reason: "replaced".into(),
                },
                context,
            )
            .await?;
        }
        self.submit_managed_order(replacement, context)
            .await
            .map(|order| command_status("accepted", Some(order.order_id.to_string())))
    }

    fn contract_health(&mut self) -> ExecutionHealthResponse {
        let routes = self
            .conflux
            .route_status
            .iter()
            .map(|(route_id, (required, status))| ExecutionRouteHealth {
                route_id: kairos_primitives::execution::ExecutionRouteId::new(route_id.clone())
                    .expect("validated execution route identity"),
                status: status.clone(),
                required: *required,
            })
            .collect::<Vec<_>>();
        let routes_ready = routes
            .iter()
            .all(|route| !route.required || route.status == "ready");
        let (pending, outbox_error) = match self.pending_outbox(1_000_000) {
            Ok(pending) => (pending, None),
            Err(error) => (Vec::new(), Some(error.to_string())),
        };
        let now = now_unix_nanos();
        let oldest_outbox_event_age_ms = pending
            .iter()
            .map(|entry| entry.created_at_unix_nanos)
            .min()
            .map(|created_at| now.saturating_sub(created_at) / 1_000_000);
        ExecutionHealthResponse {
            status: if routes_ready && self.writer_recovery_ready() && outbox_error.is_none() {
                "ready"
            } else {
                "degraded"
            }
            .into(),
            writer_recovery_ready: self.writer_recovery_ready(),
            outbox_backlog: pending.len() as u64,
            oldest_outbox_event_age_ms,
            outbox_error,
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
            let resource_key = "execution-events".to_owned();
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
                    context
                        .outputs()
                        .aeron
                        .publish(&resource_key, &bytes)
                        .map_err(|error| ExecutionError::Gateway(error.to_string()))?;
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
                },
                ExecutionOutboxEvent::Intent(event) => intents.push(event.clone()),
            }
            acknowledged.push(entry.id);
        }
        if self.conflux.audit.is_none() && !self.conflux.pending_admissions.is_empty() {
            return Err(ExecutionError::Persistence(
                "Intent admission evidence requires Execution audit persistence".into(),
            ));
        }
        if let Some(mut audit) = self.conflux.audit.take() {
            let order_events = self.drain_events();
            let intent_events = self.drain_intent_events();
            let admissions = self.conflux.pending_admissions.clone();
            let result = audit
                .publish_batch(&orders, &intents, &admissions)
                .and_then(|()| audit.publish_batch(&order_events, &intent_events, &[]));
            self.conflux.audit = Some(audit);
            result.map_err(ExecutionError::Persistence)?;
            self.conflux.pending_admissions.clear();
        }
        self.acknowledge_outbox(&acknowledged)
    }

    fn publish_views(
        &mut self,
        context: &mut Context<'_, Self>,
        actor_id: &str,
    ) -> Result<(), ExecutionError> {
        use kairos_execution_contract::{ExecutionViewKey, ExecutionViewKind};
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
            let key = ExecutionViewKey::from_identity(&self.conflux.identity, kind.clone());
            let bytes = match kind {
                ExecutionViewKind::ActiveOrders => {
                    crate::services::publication::encode_active_orders(
                        actor_id,
                        &self.conflux.identity,
                        snapshot.generation.get(),
                        &key,
                        &snapshot,
                    )
                },
                ExecutionViewKind::CurrentExecution => {
                    crate::services::publication::encode_current_execution(
                        actor_id,
                        &self.conflux.identity,
                        snapshot.generation.get(),
                        &key,
                        &snapshot,
                    )
                },
                ExecutionViewKind::ActiveIntents => {
                    crate::services::publication::encode_active_intents(
                        actor_id,
                        &self.conflux.identity,
                        snapshot.generation.get(),
                        &key,
                        &snapshot,
                    )
                },
            }
            .map_err(ExecutionError::Gateway)?;
            let resource_key = key.canonical_key();
            context
                .outputs()
                .mmap
                .publish(&resource_key, metadata, &bytes)
                .map_err(|error| ExecutionError::Gateway(error.to_string()))?;
        }
        Ok(())
    }
}

fn register_execution_stream(
    context: &mut Context<'_, ExecutionApplication>,
    plan: &ExecutionConnectionPlan,
) -> Result<(), ExecutionError> {
    let key = ConnectionKey::new(plan.stream_key.clone()).map_err(ExecutionError::Gateway)?;
    macro_rules! register {
        ($family:ident) => {
            if context.connections().$family.keys().contains(&key) {
                return Ok(());
            }
        };
    }
    register!(binance_spot_user_websocket);
    register!(binance_margin_user_websocket);
    register!(binance_usdm_user_websocket);
    register!(binance_coinm_user_websocket);
    register!(binance_options_user_websocket);
    register!(binance_stocks_user_websocket);
    register!(okx_private_websocket);
    register!(ibkr_execution_stream);
    Err(ExecutionError::Gateway(format!(
        "missing managed Execution stream: {key}"
    )))
}

fn remote_order_event(
    envelope: kairos_conflux::ExternalEventEnvelope<kairos_conflux::ExternalExecutionEvent>,
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
        connection_id: envelope.connection_key.to_string(),
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

fn decimal(value: kairos_conflux::DecimalValue) -> String {
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
    if negative { format!("-{body}") } else { body }
}

fn parse_route_query(
    query: &kairos_execution_contract::ExecutionRoutesQuery,
) -> Result<ExecutionRouteQuery, ExecutionError> {
    Ok(ExecutionRouteQuery {
        account_id: query.account_id.clone(),
        segment_key: query.segment_key.clone(),
        instrument_id: query.instrument_id.clone(),
        market_id: query.market_id.clone(),
        ..Default::default()
    })
}

fn route_response(
    route: super::ExecutionRouteCandidate,
) -> Result<ExecutionRouteCandidateResponse, ExecutionError> {
    Ok(ExecutionRouteCandidateResponse {
        route_id: route.route_id,
        account_id: route.account_id,
        segment_key: route.segment_key,
        instrument_id: route.instrument_id,
        market_id: route.market_id,
        participant_id: kairos_primitives::integration::ParticipantId::new(route.participant_id)
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
        provider_product: route.provider_product,
        provider_symbol: route.provider_symbol,
        supported_order_types: route.supported_order_types,
        supported_options: route
            .supported_options
            .into_iter()
            .map(kairos_primitives::execution::OrderOptionCode::new)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
        ready: route.ready,
    })
}

fn decode_contract_intent(
    request: ExecutionIntentRequest,
) -> Result<ExecuteStrategyIntent, ExecutionError> {
    use crate::domain::{CompletionPolicy, FailurePolicy, HedgePolicy, IntentType};

    let options = decode_contract_options;
    let leg = |value: ContractIntentLegRequest| crate::application::IntentLegRequest {
        leg_id: value.leg_id,
        account_id: value.account_id,
        segment_key: value.segment_key,
        instrument_id: value.instrument_id,
        market_id: value.market_id,
        execution_route_id: value.execution_route_id,
        side: value.side,
        quantity: value.quantity,
        limit_price: value.limit_price,
        target_position: value.target_position,
        options: options(value.options),
    };
    let intent_type = match request.intent_type {
        ContractIntentType::SingleOrder => IntentType::SingleOrder,
        ContractIntentType::TargetPosition => IntentType::TargetPosition,
        ContractIntentType::PairArbitrage => IntentType::PairArbitrage,
        ContractIntentType::OptionSpread => IntentType::OptionSpread,
        ContractIntentType::PortfolioRebalance => IntentType::PortfolioRebalance,
        ContractIntentType::QuoteProvisioning => IntentType::QuoteProvisioning,
        ContractIntentType::Hedge => IntentType::Hedge,
    };
    let completion_policy = match request.completion_policy {
        ContractCompletionPolicy::AllLegsSatisfied => CompletionPolicy::AllLegsSatisfied,
        ContractCompletionPolicy::AllOrNothing => CompletionPolicy::AllOrNothing,
        ContractCompletionPolicy::BestEffort => CompletionPolicy::BestEffort,
        ContractCompletionPolicy::HedgeWithinTolerance => CompletionPolicy::HedgeWithinTolerance,
        ContractCompletionPolicy::TargetQuantityReached => CompletionPolicy::TargetQuantityReached,
    };
    let failure_policy = match request.failure_policy {
        ContractFailurePolicy::CancelRemaining => FailurePolicy::CancelRemaining,
        ContractFailurePolicy::ContinueOtherLegs => FailurePolicy::ContinueOtherLegs,
        ContractFailurePolicy::Compensate => FailurePolicy::Compensate,
        ContractFailurePolicy::PauseForManualIntervention => {
            FailurePolicy::PauseForManualIntervention
        },
        ContractFailurePolicy::MarkReconciliationRequired => {
            FailurePolicy::MarkReconciliationRequired
        },
    };
    let hedge_policy = request
        .hedge_policy
        .map(|value: HedgePolicyRequest| HedgePolicy {
            leader_leg_id: value.leader_leg_id,
            hedge_leg_id: value.hedge_leg_id,
            ratio: value.ratio,
            contract_multiplier: value.contract_multiplier,
            max_unhedged_quantity: value.max_unhedged_quantity,
            compensate_on_failure: value.compensate_on_failure,
            max_compensation_attempts: value.max_compensation_attempts,
        });
    Ok(ExecuteStrategyIntent {
        intent_id: request.intent_id,
        strategy_decision_id: request.strategy_decision_id.map(|value| value.to_string()),
        strategy_id: request.strategy_id.to_string(),
        launch_id: request.launch_id.to_string(),
        instance_id: request.instance_id.to_string(),
        instrument_id: request.instrument_id,
        market_id: request.market_id,
        execution_route_id: request.execution_route_id,
        account_ids: request.account_ids,
        segment_key: request.segment_key,
        target_quantity: request.target_quantity,
        limit_price: request.limit_price,
        source_snapshot_id: request.source_snapshot_id,
        source_event_sequence: request.source_event_sequence,
        source_event_time_unix_nanos: request.source_event_time_unix_nanos,
        reason: request.reason,
        intent_type,
        completion_policy,
        failure_policy,
        legs: request.legs.into_iter().map(leg).collect(),
        deadline_unix_nanos: request.deadline_unix_nanos,
        min_edge_bps: request.min_edge_bps,
        max_slippage_bps: request.max_slippage_bps,
        estimated_fee_bps: request.estimated_fee_bps,
        minimum_net_credit: request.minimum_net_credit,
        maximum_loss: request.maximum_loss,
        hedge_policy,
        order_options: options(request.order_options),
    })
}

fn decode_contract_options(value: ExecutionOrderOptionsRequest) -> ExecutionOrderOptions {
    ExecutionOrderOptions {
        time_in_force: value.time_in_force,
        reduce_only: value.reduce_only,
        post_only: value.post_only,
        position_side: value.position_side,
        quote_asset: value.quote_asset,
        wallet_type: value.wallet_type,
        trading_session: value.trading_session,
        tokenize: value.tokenize,
        split: value.split.map(|split| crate::domain::SplitOrderPolicy {
            max_child_quantity: split.max_child_quantity,
            child_count: split.child_count,
            min_child_quantity: split.min_child_quantity,
            interval: split.interval,
        }),
        maker: value
            .maker
            .map(|maker| crate::domain::MakerExecutionPolicy {
                min_interval: maker.min_interval,
                max_orders_per_window: maker.max_orders_per_window,
                window: maker.window,
                max_inventory_abs: maker.max_inventory_abs,
                target_inventory: maker.target_inventory,
                max_quote_age: maker.max_quote_age,
            }),
    }
}

fn decode_intent_admission_evidence(
    raw: Option<IntentAdmissionEvidenceRequest>,
    submitted: &ExecuteStrategyIntent,
) -> Result<Option<IntentAdmissionEvidence>, ExecutionError> {
    let Some(raw) = raw else {
        return Ok(None);
    };
    let original = decode_contract_intent(raw.original_intent)?;
    let effective = decode_contract_intent(raw.effective_intent)?;
    let original_hash = canonical_typed_hash(&original)?;
    let effective_hash = canonical_typed_hash(&effective)?;
    if raw.source != "decision_agent" {
        return Err(ExecutionError::Invalid(
            "unsupported Intent admission evidence source".into(),
        ));
    }
    if raw.decision_id.as_str().is_empty() || raw.decision_id.as_str().len() > 256 {
        return Err(ExecutionError::Invalid(
            "Intent admission evidence decision_id is invalid".into(),
        ));
    }
    if !matches!(raw.outcome.as_str(), "approved" | "revised") {
        return Err(ExecutionError::Invalid(
            "unsupported Intent admission evidence outcome".into(),
        ));
    }
    if raw.original_hash != original_hash || raw.effective_hash != effective_hash {
        return Err(ExecutionError::Invalid(
            "Intent admission evidence hash mismatch".into(),
        ));
    }
    if canonical_typed_hash(submitted)? != effective_hash {
        return Err(ExecutionError::Invalid(
            "Intent admission evidence effective Intent differs from submission".into(),
        ));
    }
    if effective != *submitted {
        return Err(ExecutionError::Invalid(
            "Intent admission evidence effective Intent failed typed equality".into(),
        ));
    }
    if !same_revision_identity(&original, &effective) {
        return Err(ExecutionError::Invalid(
            "Intent admission revision changed immutable Intent fields".into(),
        ));
    }
    match raw.outcome.as_str() {
        "approved" if original != effective || original_hash != effective_hash => {
            return Err(ExecutionError::Invalid(
                "approved Intent admission evidence contains a revision".into(),
            ));
        },
        "revised" if original == effective => {
            return Err(ExecutionError::Invalid(
                "revised Intent admission evidence contains no revision".into(),
            ));
        },
        _ => {},
    }
    Ok(Some(IntentAdmissionEvidence {
        source: raw.source,
        decision_id: raw.decision_id.to_string(),
        outcome: raw.outcome,
        original_intent: original,
        effective_intent: effective,
        original_hash: raw.original_hash,
        effective_hash: raw.effective_hash,
    }))
}

fn canonical_typed_hash<T: serde::Serialize>(value: &T) -> Result<String, ExecutionError> {
    let bytes = serde_json::to_vec(value)
        .map_err(|error| ExecutionError::Invalid(format!("cannot canonicalize Intent: {error}")))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

#[cfg(test)]
fn canonical_value_hash(value: &serde_json::Value) -> Result<String, ExecutionError> {
    let bytes = serde_json::to_vec(value)
        .map_err(|error| ExecutionError::Invalid(format!("cannot canonicalize Intent: {error}")))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

fn same_revision_identity(
    original: &ExecuteStrategyIntent,
    effective: &ExecuteStrategyIntent,
) -> bool {
    let mut normalized = original.clone();
    normalized.target_quantity = effective.target_quantity;
    normalized.limit_price = effective.limit_price;
    normalized.deadline_unix_nanos = effective.deadline_unix_nanos;
    normalized.max_slippage_bps = effective.max_slippage_bps;
    normalized.order_options = effective.order_options.clone();
    normalized == *effective
}

fn command_status(status: &str, order_id: Option<String>) -> ExecutionCommandStatus {
    ExecutionCommandStatus {
        status: status.into(),
        command_id: None,
        intent_id: None,
        order_id: order_id.map(|value| {
            kairos_primitives::execution::OrderId::new(value)
                .expect("execution order identity is validated")
        }),
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

#[cfg(test)]
mod admission_tests {
    use super::canonical_value_hash;
    use crate::application::ExecuteStrategyIntent;

    fn evidence(
        original: &ExecuteStrategyIntent,
        effective: &ExecuteStrategyIntent,
        outcome: &str,
    ) -> serde_json::Value {
        let original = serde_json::to_value(original).unwrap();
        let effective = serde_json::to_value(effective).unwrap();
        serde_json::json!({
            "source": "decision_agent",
            "decision_id": "decision-1",
            "outcome": outcome,
            "original_hash": canonical_value_hash(&original).unwrap(),
            "effective_hash": canonical_value_hash(&effective).unwrap(),
            "original_intent": original,
            "effective_intent": effective,
        })
    }
}
