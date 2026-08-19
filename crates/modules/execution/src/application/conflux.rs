use std::collections::BTreeMap;
use std::time::Duration;

use kairos_conflux::ExternalParticipantEvent;
use kairos_conflux::{
    CommandOutcome, ConfluxActor, ConfluxEvent, ConnectionKey, Context, Contract, IntegrationError,
    IntegrationEvent, OrderCommand, OrderEntryEvent, OrderEntryRequest, ResourceOperationError,
    RestContract, SystemEvent,
};
use kairos_execution_contract::{
    ExecutionCommandStatus, ExecutionControlError, ExecutionHealthResponse,
    ExecutionReconcileResponse, ExecutionRestRequest, ExecutionRestResponse,
    ExecutionRouteCandidateResponse, ExecutionRouteHealth, ExecutionRoutesResponse,
};
use kairos_protocol::InstanceIdentity;
use kairos_transport::SnapshotEnvelopeMetadata;
use serde::Deserialize;
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
    view_root: std::path::PathBuf,
    view_slot_size: usize,
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
            view_root: std::path::PathBuf::new(),
            view_slot_size: 4 * 1024 * 1024,
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
                }
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
                }
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
                }
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
                }
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
                    }
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
                        }
                        Err(error) => {
                            tracing::warn!(component = "execution", error = %error, "Execution rejected provider event")
                        }
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
                self.maintain(context).await?;
                None
            }
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
                let command_id = request.envelope.command_id;
                let idempotency_key = request
                    .envelope
                    .idempotency_key
                    .or(command_id.clone())
                    .ok_or_else(|| ExecutionError::Invalid("idempotency_key is required".into()));
                let intent_raw = request.intent;
                let decoded = serde_json::from_value::<ExecuteStrategyIntent>(intent_raw.clone())
                    .map_err(|error| ExecutionError::Invalid(error.to_string()));
                let prepared = decoded.and_then(|intent| {
                    let evidence = decode_intent_admission_evidence(
                        request.admission_evidence,
                        &intent_raw,
                        &intent,
                    )?;
                    Ok((intent, evidence))
                });
                let mut admission_result = "rejected".to_owned();
                let mut admission: Option<(IntentAdmissionEvidence, String, String)> = None;
                let result = match (prepared, idempotency_key) {
                    (Ok((intent, evidence)), Ok(key)) => {
                        if let Some(evidence) = evidence {
                            admission = Some((evidence, key.clone(), intent.intent_id.to_string()));
                        }
                        match self.accept_intent_with_idempotency_deferred(intent, key) {
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
                                            intent_id: Some(intent.intent.intent_id.to_string()),
                                            order_id: None,
                                        })
                                    }
                                } else {
                                    Ok(ExecutionCommandStatus {
                                        status: "duplicate".into(),
                                        command_id: command_id.clone(),
                                        intent_id: Some(intent.intent.intent_id.to_string()),
                                        order_id: None,
                                    })
                                }
                            }
                            Err(error) => Err(error),
                        }
                    }
                    (Err(error), _) | (_, Err(error)) => Err(error),
                };
                if let Some((evidence, idempotency_key, intent_id)) = admission {
                    self.conflux
                        .pending_admissions
                        .push(IntentAdmissionAuditRecord {
                            command_id,
                            idempotency_key,
                            intent_id,
                            evidence,
                            admission_result,
                            created_at_unix_nanos: now_unix_nanos(),
                        });
                }
                ExecutionRestResponse::SubmitIntent(result.map_err(control_error))
            }
            ExecutionRestRequest::CancelOrder { order_id, request } => {
                let result = match kairos_primitives::OrderId::new(order_id) {
                    Ok(order_id) => {
                        self.cancel_managed_order(
                            CancelOrder {
                                order_id,
                                reason: request.reason.unwrap_or_default(),
                            },
                            context,
                        )
                        .await
                    }
                    Err(error) => Err(ExecutionError::Invalid(error.to_string())),
                }
                .map(|order| command_status("accepted", Some(order.order_id.to_string())));
                ExecutionRestResponse::CancelOrder(result.map_err(control_error))
            }
            ExecutionRestRequest::ReplaceOrder { order_id, request } => {
                let result = self
                    .replace_contract_order(order_id, request, context)
                    .await;
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
                    match query {
                        Ok(query) => self
                            .reconcile_managed_orders(query, context)
                            .await
                            .map(|changed| ExecutionReconcileResponse { changed }),
                        Err(error) => Err(error),
                    }
                    .map_err(control_error),
                )
            }
        }
    }

    async fn replace_contract_order(
        &mut self,
        order_id: String,
        patch: kairos_execution_contract::ReplaceOrderRequest,
        context: &mut Context<'_, Self>,
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
                route_id: route_id.clone(),
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
            outbox_backlog: pending.len(),
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
                        .system()
                        .execution_event_publishers
                        .try_with(&resource_key, |publisher| publisher.publish(&bytes))
                        .map_err(|error| match error {
                            ResourceOperationError::NotFound => ExecutionError::Gateway(
                                "missing execution-events Aeron publisher".into(),
                            ),
                            ResourceOperationError::Operation(error) => {
                                ExecutionError::Gateway(error.to_string())
                            }
                        })?;
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
                .try_with(&resource_key, |publisher| {
                    publisher.publish(metadata, &bytes)
                })
                .map_err(|error| match error {
                    ResourceOperationError::NotFound => {
                        ExecutionError::Gateway("Execution view publisher disappeared".into())
                    }
                    ResourceOperationError::Operation(error) => {
                        ExecutionError::Gateway(error.to_string())
                    }
                })?;
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

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IntentAdmissionEvidenceWire {
    source: String,
    decision_id: String,
    outcome: String,
    original_intent: ExecuteStrategyIntent,
    effective_intent: ExecuteStrategyIntent,
    original_hash: String,
    effective_hash: String,
}

fn decode_intent_admission_evidence(
    raw: Option<serde_json::Value>,
    submitted_raw: &serde_json::Value,
    submitted: &ExecuteStrategyIntent,
) -> Result<Option<IntentAdmissionEvidence>, ExecutionError> {
    let Some(raw) = raw else {
        return Ok(None);
    };
    let original_hash = canonical_value_hash(raw.get("original_intent").ok_or_else(|| {
        ExecutionError::Invalid("Intent admission evidence original_intent is required".into())
    })?)?;
    let effective_hash = canonical_value_hash(raw.get("effective_intent").ok_or_else(|| {
        ExecutionError::Invalid("Intent admission evidence effective_intent is required".into())
    })?)?;
    let wire: IntentAdmissionEvidenceWire = serde_json::from_value(raw)
        .map_err(|error| ExecutionError::Invalid(format!("invalid admission evidence: {error}")))?;
    if wire.source != "decision_agent" {
        return Err(ExecutionError::Invalid(
            "unsupported Intent admission evidence source".into(),
        ));
    }
    if wire.decision_id.trim().is_empty() || wire.decision_id.len() > 256 {
        return Err(ExecutionError::Invalid(
            "Intent admission evidence decision_id is invalid".into(),
        ));
    }
    if !matches!(wire.outcome.as_str(), "approved" | "revised") {
        return Err(ExecutionError::Invalid(
            "unsupported Intent admission evidence outcome".into(),
        ));
    }
    if wire.original_hash != original_hash || wire.effective_hash != effective_hash {
        return Err(ExecutionError::Invalid(
            "Intent admission evidence hash mismatch".into(),
        ));
    }
    if canonical_value_hash(submitted_raw)? != effective_hash {
        return Err(ExecutionError::Invalid(
            "Intent admission evidence effective Intent differs from submission".into(),
        ));
    }
    let original = wire.original_intent;
    let effective = wire.effective_intent;
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
    match wire.outcome.as_str() {
        "approved" if original != effective || original_hash != effective_hash => {
            return Err(ExecutionError::Invalid(
                "approved Intent admission evidence contains a revision".into(),
            ));
        }
        "revised" if original == effective => {
            return Err(ExecutionError::Invalid(
                "revised Intent admission evidence contains no revision".into(),
            ));
        }
        _ => {}
    }
    Ok(Some(IntentAdmissionEvidence {
        source: wire.source,
        decision_id: wire.decision_id,
        outcome: wire.outcome,
        original_intent: original,
        effective_intent: effective,
        original_hash: wire.original_hash,
        effective_hash: wire.effective_hash,
    }))
}

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

#[cfg(test)]
mod admission_tests {
    use super::{canonical_value_hash, decode_intent_admission_evidence};
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

    #[test]
    fn admission_evidence_accepts_approved_and_typed_revision() {
        let original = ExecuteStrategyIntent::default();
        let submitted = serde_json::to_value(&original).unwrap();
        let approved = decode_intent_admission_evidence(
            Some(evidence(&original, &original, "approved")),
            &submitted,
            &original,
        )
        .unwrap()
        .unwrap();
        assert_eq!(approved.original_intent, approved.effective_intent);

        let mut effective = original.clone();
        effective.target_quantity = kairos_primitives::Quantity::new(1, 0).unwrap();
        let submitted = serde_json::to_value(&effective).unwrap();
        let revised = decode_intent_admission_evidence(
            Some(evidence(&original, &effective, "revised")),
            &submitted,
            &effective,
        )
        .unwrap()
        .unwrap();
        assert_ne!(revised.original_intent, revised.effective_intent);
    }

    #[test]
    fn admission_evidence_rejects_hash_and_identity_changes() {
        let original = ExecuteStrategyIntent::default();
        let mut effective = original.clone();
        effective.strategy_id = "different-strategy".into();
        let submitted = serde_json::to_value(&effective).unwrap();
        let error = decode_intent_admission_evidence(
            Some(evidence(&original, &effective, "revised")),
            &submitted,
            &effective,
        )
        .unwrap_err();
        assert!(error.to_string().contains("immutable"));

        let submitted = serde_json::to_value(&original).unwrap();
        let mut corrupt = evidence(&original, &original, "approved");
        corrupt["effective_hash"] = serde_json::Value::String("0".repeat(64));
        let error =
            decode_intent_admission_evidence(Some(corrupt), &submitted, &original).unwrap_err();
        assert!(error.to_string().contains("hash mismatch"));
    }
}
