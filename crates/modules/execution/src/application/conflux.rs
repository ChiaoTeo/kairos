use std::collections::BTreeMap;
use std::convert::Infallible;
use std::time::Duration;

use kairos_conflux::{
    CommandOutcome, ConfluxActor, ConfluxEvent, ConnectionKey, Context, ExternalParticipantEvent,
    IntegrationError, IntegrationEvent, OrderCommand, OrderEntryEvent, OrderEntryRequest,
    SnapshotEnvelopeMetadata, SystemEvent,
};
use kairos_execution_contract::{
    AdvanceExecutionTimeRequest, AdvanceExecutionTimeResponse, CancelOrderRequest,
    CompletionPolicy as ContractCompletionPolicy, ExecutionAlgorithmPolicyRequest,
    ExecutionBacktestBar, ExecutionBacktestMarketObservation, ExecutionBacktestMarketRequest,
    ExecutionBacktestMarketResponse, ExecutionBacktestMetrics, ExecutionBacktestObservationScope,
    ExecutionBacktestOrder, ExecutionBacktestOrderRequest, ExecutionBacktestOrderStatus,
    ExecutionBacktestRequest, ExecutionBacktestRunResponse, ExecutionBacktestSimulationConfig,
    ExecutionBacktestSimulationFill, ExecutionCommandStatus, ExecutionControlError,
    ExecutionHealthResponse, ExecutionIntentRequest, ExecutionOrderOptionsRequest,
    ExecutionReconcileResponse, ExecutionRouteCandidateResponse, ExecutionRouteHealth,
    ExecutionRoutesQuery, ExecutionRoutesResponse, FailurePolicy as ContractFailurePolicy,
    HedgePolicyRequest, IntentAdmissionEvidenceRequest,
    IntentLegRequest as ContractIntentLegRequest, IntentType as ContractIntentType,
    ReconcileExecutionRequest, ReplaceOrderRequest, SubmitIntentRequest,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::control::jsonrpc::{ErrorObjectOwned, RpcResult, business_error};
use sha2::{Digest, Sha256};

use super::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestMetrics, BacktestRequest, Bar,
    CancelOrder, ExecuteStrategyIntent, ExecutionAlgorithmPolicy, ExecutionApplication,
    ExecutionError, ExecutionOrderOptions, ExecutionRouteQuery, ExecutionRpcActor,
    IntentAdmissionEvidence, MarketObservation, ObservationScope, Quote, QuoteBar,
    RemoteOrderQuery, SubmitOrder, TradeBar,
};
use crate::domain::{AlgorithmExecutionStyle, ExecutionAlgorithmSpec};
use crate::services::actor::RemoteOrderEvent;
use crate::services::audit::{ExecutionAudit, IntentAdmissionAuditRecord};
use crate::services::gateway::{ExecutionConnectionPlan, ExecutionWriterFence};
use crate::services::persistence::ExecutionOutboxEvent;
use crate::services::simulation::{
    SimulatedAccountSettlement, SimulationConfig, SimulationFill, SimulationOrder,
    SimulationOrderRequest, SimulationOrderStatus,
};

const EXECUTION_BUSINESS_ERROR_CODE: i32 = -31_004;

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
        self.advance_due_algorithm_runs_managed(business_now, 64, context)
            .await?;
        self.advance_due_intent_orders_managed(business_now, 64, context)
            .await?;
        self.expire_due_intents_managed(business_now, context)
            .await?;
        Ok(())
    }

    pub(crate) async fn advance_due_algorithm_runs_managed(
        &mut self,
        now_unix_nanos: u64,
        limit: usize,
        context: &mut Context<'_, Self>,
    ) -> Result<usize, ExecutionError> {
        let due = self.prepare_due_algorithm_runs(now_unix_nanos, limit)?;
        if !due.is_empty() {
            self.advance_due_intent_orders_managed(now_unix_nanos, usize::MAX, context)
                .await?;
        }
        Ok(due.len())
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
                    if due.execution_style == AlgorithmExecutionStyle::TakerImmediate
                        && !matches!(error, ExecutionError::Indeterminate(_))
                        && self
                            .intent(due.intent_id.as_str())
                            .is_some_and(|state| !state.pending_orders.is_empty())
                    {
                        continue;
                    }
                    if matches!(
                        due.execution_style,
                        AlgorithmExecutionStyle::TakerImmediate
                            | AlgorithmExecutionStyle::UnwindImmediate
                    ) {
                        break;
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
        if self
            .algorithm_runs()
            .into_iter()
            .find(|run| run.intent_id.as_str() == intent_id)
            .is_some_and(|run| matches!(run.spec, ExecutionAlgorithmSpec::MakerTakerHedge(_)))
        {
            let business_now = self
                .business_time_unix_nanos()
                .unwrap_or_else(now_unix_nanos);
            self.drive_maker_taker_hedge(intent_id, business_now)?;
            let due_at = self
                .algorithm_runs()
                .into_iter()
                .find(|run| run.intent_id.as_str() == intent_id)
                .and_then(|run| run.last_decision_at)
                .map(|value| value.get())
                .unwrap_or(business_now);
            self.advance_due_intent_orders_managed(due_at, usize::MAX, context)
                .await?;
            return Ok(());
        }
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
    type LocalEvent = Infallible;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.register_managed_streams(context)?;
        context.spawn_timer("maintenance", Duration::from_secs(1));
        self.publish(context)?;
        Ok(())
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent,
        context: &mut Context<'_, Self>,
    ) -> Result<(), Self::FatalError> {
        match event {
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
            },
            ConfluxEvent::System(SystemEvent::SourceReady { source }) => {
                self.update_route_status(&source, "ready");
            },
            ConfluxEvent::System(SystemEvent::SourceFailed { source, error }) => {
                self.update_route_status(&source, "degraded");
                tracing::warn!(component = "execution", %source, %error, "Execution source failed");
            },
            ConfluxEvent::System(SystemEvent::Timer { name, .. }) if name == "maintenance" => {
                self.maintain(context).await?;
            },
            _ => {},
        };
        self.publish(context)?;
        Ok(())
    }

    async fn stopping(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.publish(context)
    }
}

impl ExecutionRpcActor for ExecutionApplication {
    async fn health(
        &mut self,
        (): (),
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ExecutionHealthResponse> {
        let response = self.contract_health();
        self.publish(context).map_err(rpc_execution_error)?;
        Ok(response)
    }

    async fn routes(
        &mut self,
        query: ExecutionRoutesQuery,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ExecutionRoutesResponse> {
        let participant = query.broker_id.clone();
        let query = parse_route_query(&query).map_err(rpc_execution_error)?;
        let routes = self
            .available_execution_routes(&query)
            .into_iter()
            .filter(|route| {
                participant
                    .as_ref()
                    .is_none_or(|value| route.broker_id.eq_ignore_ascii_case(value.as_str()))
            })
            .map(route_response)
            .collect::<Result<Vec<_>, _>>()
            .map_err(rpc_execution_error)?;
        self.publish(context).map_err(rpc_execution_error)?;
        Ok(ExecutionRoutesResponse { routes })
    }

    async fn submit_intent(
        &mut self,
        request: SubmitIntentRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ExecutionCommandStatus> {
        let response = self.submit_intent_control(request, context).await?;
        self.publish(context).map_err(rpc_execution_error)?;
        Ok(response)
    }

    async fn cancel_order(
        &mut self,
        (order_id, request): (kairos_primitives::execution::OrderId, CancelOrderRequest),
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ExecutionCommandStatus> {
        let response = self
            .cancel_managed_order(
                CancelOrder {
                    order_id,
                    reason: request.reason.unwrap_or_default(),
                },
                context,
            )
            .await
            .map(|order| command_status("accepted", Some(order.order_id.to_string())))
            .map_err(rpc_execution_error)?;
        self.publish(context).map_err(rpc_execution_error)?;
        Ok(response)
    }

    async fn replace_order(
        &mut self,
        (order_id, request): (kairos_primitives::execution::OrderId, ReplaceOrderRequest),
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ExecutionCommandStatus> {
        let response = self
            .replace_contract_order(order_id, request, context)
            .await
            .map_err(rpc_execution_error)?;
        self.publish(context).map_err(rpc_execution_error)?;
        Ok(response)
    }

    async fn reconcile(
        &mut self,
        request: ReconcileExecutionRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ExecutionReconcileResponse> {
        let query = RemoteOrderQuery {
            binding_id: request
                .execution_route_id
                .map(|route| format!("execution.{route}.query")),
            order_id: request.order_id,
            limit: Some(200),
            ..Default::default()
        };
        let changed = self
            .reconcile_managed_orders(query, context)
            .await
            .map_err(rpc_execution_error)?;
        self.publish(context).map_err(rpc_execution_error)?;
        Ok(ExecutionReconcileResponse {
            changed: changed as u64,
        })
    }

    async fn advance_time(
        &mut self,
        request: AdvanceExecutionTimeRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<AdvanceExecutionTimeResponse> {
        self.advance_time(request.event_time_unix_nanos.get())
            .map_err(rpc_execution_error)?;
        self.advance_due_algorithm_runs_managed(
            request.event_time_unix_nanos.get(),
            usize::MAX,
            context,
        )
        .await
        .map_err(rpc_execution_error)?;
        self.advance_due_intent_orders_managed(
            request.event_time_unix_nanos.get(),
            usize::MAX,
            context,
        )
        .await
        .map_err(rpc_execution_error)?;
        self.publish(context).map_err(rpc_execution_error)?;
        Ok(AdvanceExecutionTimeResponse {
            advanced_to_unix_nanos: request.event_time_unix_nanos,
        })
    }

    async fn backtest_run(
        &mut self,
        request: ExecutionBacktestRequest,
        _context: &mut Context<'_, Self>,
    ) -> RpcResult<ExecutionBacktestRunResponse> {
        BacktestApplication::run(backtest_request(request))
            .map(backtest_run_response)
            .map_err(|error| rpc_execution_error(ExecutionError::Invalid(error)))
    }

    async fn backtest_market(
        &mut self,
        request: ExecutionBacktestMarketRequest,
        _context: &mut Context<'_, Self>,
    ) -> RpcResult<ExecutionBacktestMarketResponse> {
        BacktestApplication::run(BacktestRequest {
            initial_equity: "0".parse().expect("zero money is valid"),
            market_events: vec![market_observation(request.event)],
            ..BacktestRequest::default()
        })
        .map(|response| ExecutionBacktestMarketResponse {
            fills: response
                .fills
                .into_iter()
                .map(simulation_fill_response)
                .collect(),
        })
        .map_err(|error| rpc_execution_error(ExecutionError::Invalid(error)))
    }
}

impl ExecutionApplication {
    async fn submit_intent_control(
        &mut self,
        request: SubmitIntentRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ExecutionCommandStatus> {
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
            let evidence = decode_intent_admission_evidence(request.admission_evidence, &intent)?;
            Ok((intent, evidence))
        });
        let mut admission_result = "rejected".to_owned();
        let mut admission: Option<(IntentAdmissionEvidence, String, String)> = None;
        let result = match (prepared, idempotency_key) {
            (Ok((intent, evidence)), Ok(key)) => {
                if let Some(evidence) = evidence {
                    admission = Some((evidence, key.to_string(), intent.intent_id.to_string()));
                }
                match self.accept_intent_with_idempotency_deferred(intent, key.to_string()) {
                    Ok((intent, duplicate)) => {
                        admission_result = if duplicate { "duplicate" } else { "accepted" }.into();
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
        result.map_err(rpc_execution_error)
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

fn backtest_request(request: ExecutionBacktestRequest) -> BacktestRequest {
    BacktestRequest {
        initial_equity: request.initial_equity,
        equity_curve: request
            .equity_curve
            .into_iter()
            .map(|point| BacktestEquityPoint {
                observed_at_unix_nanos: point.observed_at_unix_nanos,
                equity: point.equity,
            })
            .collect(),
        fills: request
            .fills
            .into_iter()
            .map(|fill| BacktestFill {
                instrument_id: fill.instrument_id,
                side: fill.side,
                quantity: fill.quantity,
                price: fill.price,
                fee: fill.fee,
                occurred_at_unix_nanos: fill.occurred_at_unix_nanos,
            })
            .collect(),
        risk_free_rate: request.risk_free_rate,
        annualization_periods: request.annualization_periods,
        market_events: request
            .market_events
            .into_iter()
            .map(market_observation)
            .collect(),
        orders: request
            .orders
            .into_iter()
            .map(simulation_order_request)
            .collect(),
        simulation: simulation_config(request.simulation),
    }
}

fn market_observation(value: ExecutionBacktestMarketObservation) -> MarketObservation {
    match value {
        ExecutionBacktestMarketObservation::Quote(value) => MarketObservation::Quote(Quote {
            scope: observation_scope(value.scope),
            instrument_id: value.instrument_id,
            bid_price: value.bid_price,
            bid_quantity: value.bid_quantity,
            ask_price: value.ask_price,
            ask_quantity: value.ask_quantity,
            observed_at_unix_nanos: value.observed_at_unix_nanos,
            source_id: value.source_id,
        }),
        ExecutionBacktestMarketObservation::Bar(value) => MarketObservation::Bar(bar(value)),
        ExecutionBacktestMarketObservation::TradeBar(value) => {
            MarketObservation::TradeBar(TradeBar {
                bar: bar(value.bar),
            })
        },
        ExecutionBacktestMarketObservation::QuoteBar(value) => {
            MarketObservation::QuoteBar(QuoteBar {
                bar: bar(value.bar),
            })
        },
    }
}

fn observation_scope(value: ExecutionBacktestObservationScope) -> ObservationScope {
    match value {
        ExecutionBacktestObservationScope::Market { market_id } => {
            ObservationScope::Market { market_id }
        },
        ExecutionBacktestObservationScope::Consolidated {
            instrument_id,
            network_id,
        } => ObservationScope::Consolidated {
            instrument_id,
            network_id,
        },
    }
}

fn bar(value: ExecutionBacktestBar) -> Bar {
    Bar {
        scope: observation_scope(value.scope),
        instrument_id: value.instrument_id,
        timeframe: value.timeframe,
        open: value.open,
        high: value.high,
        low: value.low,
        close: value.close,
        volume: value.volume,
        observed_at_unix_nanos: value.observed_at_unix_nanos,
        source_id: value.source_id,
        derivation: value.derivation,
    }
}

fn simulation_config(value: ExecutionBacktestSimulationConfig) -> SimulationConfig {
    SimulationConfig {
        fee_bps: value.fee_bps,
        fee_currency: value.fee_currency,
        slippage_bps: value.slippage_bps,
        enforce_quote_quantity: value.enforce_quote_quantity,
    }
}

fn simulation_order_request(value: ExecutionBacktestOrderRequest) -> SimulationOrderRequest {
    SimulationOrderRequest {
        order_id: value.order_id,
        instrument_id: value.instrument_id,
        market_id: value.market_id,
        side: value.side,
        order_type: value.order_type,
        quantity: value.quantity,
        limit_price: value.limit_price,
        submitted_at_unix_nanos: value.submitted_at_unix_nanos,
    }
}

fn backtest_run_response(
    value: crate::application::BacktestRunResult,
) -> ExecutionBacktestRunResponse {
    ExecutionBacktestRunResponse {
        metrics: backtest_metrics(value.metrics),
        orders: value
            .orders
            .into_iter()
            .map(simulation_order_response)
            .collect(),
        fills: value
            .fills
            .into_iter()
            .map(simulation_fill_response)
            .collect(),
    }
}

fn backtest_metrics(value: BacktestMetrics) -> ExecutionBacktestMetrics {
    ExecutionBacktestMetrics {
        trade_count: value.trade_count,
        win_count: value.win_count,
        loss_count: value.loss_count,
        win_rate: value.win_rate,
        gross_profit: value.gross_profit,
        gross_loss: value.gross_loss,
        net_profit: value.net_profit,
        max_drawdown: value.max_drawdown,
        max_drawdown_pct: value.max_drawdown_pct,
        sharpe: value.sharpe,
    }
}

fn simulation_order_response(value: SimulationOrder) -> ExecutionBacktestOrder {
    ExecutionBacktestOrder {
        request: ExecutionBacktestOrderRequest {
            order_id: value.request.order_id,
            instrument_id: value.request.instrument_id,
            market_id: value.request.market_id,
            side: value.request.side,
            order_type: value.request.order_type,
            quantity: value.request.quantity,
            limit_price: value.request.limit_price,
            submitted_at_unix_nanos: value.request.submitted_at_unix_nanos,
        },
        status: match value.status {
            SimulationOrderStatus::Accepted => ExecutionBacktestOrderStatus::Accepted,
            SimulationOrderStatus::PartiallyFilled => ExecutionBacktestOrderStatus::PartiallyFilled,
            SimulationOrderStatus::Filled => ExecutionBacktestOrderStatus::Filled,
            SimulationOrderStatus::Canceled => ExecutionBacktestOrderStatus::Canceled,
            SimulationOrderStatus::Rejected => ExecutionBacktestOrderStatus::Rejected,
        },
        filled_quantity: value.filled_quantity,
        remaining_quantity: value.remaining_quantity,
        updated_at_unix_nanos: value.updated_at_unix_nanos,
        reason: value.reason,
    }
}

fn simulation_fill_response(value: SimulationFill) -> ExecutionBacktestSimulationFill {
    ExecutionBacktestSimulationFill {
        fill_id: value.fill_id,
        order_id: value.order_id,
        instrument_id: value.instrument_id,
        execution_market_id: value.execution_market_id,
        side: value.side,
        quantity: value.quantity,
        price: value.price,
        fee: value.fee,
        fee_currency: value.fee_currency,
        occurred_at_unix_nanos: value.occurred_at_unix_nanos,
    }
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
        broker_id: route.broker_id,
        execution_channel: route.execution_channel,
        order_entry_symbol: route.order_entry_symbol,
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
    let hedge_policy = |value: HedgePolicyRequest| HedgePolicy {
        leader_leg_id: value.leader_leg_id,
        hedge_leg_id: value.hedge_leg_id,
        ratio: value.ratio,
        contract_multiplier: value.contract_multiplier,
        max_unhedged_quantity: value.max_unhedged_quantity,
        max_unhedged_duration: value.max_unhedged_duration,
        fallback_execution_route_ids: value.fallback_execution_route_ids,
        compensate_on_failure: value.compensate_on_failure,
        max_compensation_attempts: value.max_compensation_attempts,
    };
    let algorithm = match request.algorithm {
        ExecutionAlgorithmPolicyRequest::Immediate => ExecutionAlgorithmPolicy::Immediate,
        ExecutionAlgorithmPolicyRequest::Twap(policy) => {
            ExecutionAlgorithmPolicy::Twap(crate::domain::TwapPolicy {
                slice_count: policy.slice_count,
                slice_interval: policy.slice_interval,
            })
        },
        ExecutionAlgorithmPolicyRequest::MakerTakerHedge(policy) => {
            ExecutionAlgorithmPolicy::MakerTakerHedge(hedge_policy(policy))
        },
    };
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
        algorithm,
        completion_policy,
        failure_policy,
        legs: request.legs.into_iter().map(leg).collect(),
        deadline_unix_nanos: request.deadline_unix_nanos,
        min_edge_bps: request.min_edge_bps,
        max_slippage_bps: request.max_slippage_bps,
        estimated_fee_bps: request.estimated_fee_bps,
        minimum_net_credit: request.minimum_net_credit,
        maximum_loss: request.maximum_loss,
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

fn rpc_execution_error(error: impl ToString) -> ErrorObjectOwned {
    let error = control_error(error);
    business_error(EXECUTION_BUSINESS_ERROR_CODE, error.message.clone(), error)
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}
