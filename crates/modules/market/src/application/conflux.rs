use std::collections::BTreeMap;
use std::time::Duration;

use kairos_conflux::{ConfluxActor, ConfluxEvent, Context, Contract, RestContract, SystemEvent};
use kairos_market_contract::{
    MarketCommandStatus, MarketControlError, MarketDataSource, MarketDataSourcesResponse,
    MarketHealthResponse, MarketReleaseOwnerResponse, MarketRestRequest, MarketRestResponse,
    MarketSubscriptionResponse,
};
use kairos_protocol::InstanceIdentity;
use kairos_transport::SnapshotEnvelopeMetadata;

use super::{resolve_market, resolve_option_markets, MarketApplication, MarketError};
use crate::domain::source::{SourceDescriptor, SourceRouteKey};
use crate::services::publication::contract::{encode_change_view, encode_event};
use crate::services::publication::HistoryQueue;
use crate::services::source::messages::SourceInput;
use crate::services::source::{
    spawn_snapshot, spawn_stream, spawn_stream_with_policy, SourceHandle, StreamFailurePolicy,
};
use crate::{ObservationSelector, SubscriptionId};

fn strategy_subscription_owner(
    launch_id: Option<&str>,
    instance_id: &str,
    strategy_id: &str,
) -> String {
    serde_json::to_string(&serde_json::json!([
        "strategy",
        launch_id.unwrap_or_default(),
        instance_id,
        strategy_id
    ]))
    .expect("strategy subscription owner is JSON-compatible")
}

pub struct MarketRest;

pub struct MarketConfluxEvent(MarketConfluxEventKind);

#[derive(Clone, Copy)]
pub(crate) enum MarketSourceMode {
    Snapshot(Duration),
    Stream,
    MarketScopedStream,
}

#[derive(Clone)]
pub(crate) struct MarketSourcePlan {
    pub(crate) descriptor: SourceDescriptor,
    pub(crate) mode: MarketSourceMode,
}

enum MarketConfluxEventKind {
    Source(SourceInput),
    Universe(super::ReconcileMarketUniverse),
}

impl RestContract for MarketRest {
    type Request = MarketRestRequest;
    type Response = MarketRestResponse;
}

impl Contract for MarketApplication {
    type Rest = MarketRest;
}

pub(crate) struct MarketConfluxState {
    freshness_interval: Duration,
    freshness_max_age: Duration,
    shutdown_timeout: Duration,
    view_root: std::path::PathBuf,
    view_slot_size: usize,
    identity: InstanceIdentity,
    producer_incarnation: u64,
    source_plans: BTreeMap<String, MarketSourcePlan>,
    history: Option<HistoryQueue>,
    universe_updates: Option<tokio::sync::mpsc::Receiver<super::ReconcileMarketUniverse>>,
    command_results: BTreeMap<String, (MarketRestRequest, MarketRestResponse)>,
}

impl Default for MarketConfluxState {
    fn default() -> Self {
        Self {
            freshness_interval: Duration::from_secs(1),
            freshness_max_age: Duration::from_secs(5),
            shutdown_timeout: Duration::from_secs(5),
            view_root: std::path::PathBuf::new(),
            view_slot_size: 4 * 1024 * 1024,
            identity: InstanceIdentity::default(),
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
            source_plans: BTreeMap::new(),
            history: None,
            universe_updates: None,
            command_results: BTreeMap::new(),
        }
    }
}

impl MarketApplication {
    pub(crate) fn configure_conflux(
        &mut self,
        freshness_interval: Duration,
        freshness_max_age: Duration,
        shutdown_timeout: Duration,
        view_root: std::path::PathBuf,
        view_slot_size: usize,
        identity: InstanceIdentity,
        source_plans: Vec<MarketSourcePlan>,
        history: Option<HistoryQueue>,
        universe_updates: Option<tokio::sync::mpsc::Receiver<super::ReconcileMarketUniverse>>,
    ) -> Result<(), String> {
        if freshness_interval.is_zero() || freshness_max_age.is_zero() || view_slot_size == 0 {
            return Err("Market Conflux intervals and view slot size must be positive".into());
        }
        self.conflux.freshness_interval = freshness_interval;
        self.conflux.freshness_max_age = freshness_max_age;
        self.conflux.shutdown_timeout = shutdown_timeout;
        self.conflux.view_root = view_root;
        self.conflux.view_slot_size = view_slot_size;
        self.conflux.identity = identity;
        self.conflux.source_plans = source_plans
            .into_iter()
            .map(|plan| (plan.descriptor.id.to_string(), plan))
            .collect();
        self.conflux.history = history;
        self.conflux.universe_updates = universe_updates;
        Ok(())
    }

    fn spawn_source_inputs(&mut self, context: &mut Context<'_, Self>) {
        for (source_id, inputs) in self.take_source_inputs() {
            context.spawn_local_receiver_map(source_id.to_string(), inputs, |input| {
                MarketConfluxEvent(MarketConfluxEventKind::Source(input))
            });
        }
    }

    fn activate_managed_sources(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), MarketError> {
        let markets = self
            .current_view()
            .subscriptions
            .into_iter()
            .flat_map(|subscription| subscription.members.into_values())
            .map(|market| (SourceRouteKey::from_market(&market), market))
            .collect::<BTreeMap<_, _>>()
            .into_values()
            .collect::<Vec<_>>();
        for market in markets {
            if self
                .actor
                .attached_sources
                .values()
                .any(|source| super::source_accepts(&source.descriptor, &market))
            {
                continue;
            }
            let candidates = self
                .conflux
                .source_plans
                .values()
                .filter(|plan| super::source_accepts(&plan.descriptor, &market))
                .cloned()
                .collect::<Vec<_>>();
            let [plan] = candidates.as_slice() else {
                return Err(MarketError::SourceUnavailable(if candidates.is_empty() {
                    format!(
                        "no managed Market connection supports {}",
                        market.scope.key()
                    )
                } else {
                    format!(
                        "multiple managed Market connections support {}; select source_id",
                        market.scope.key()
                    )
                }));
            };
            let handle = take_managed_source(context.system(), plan, self.source_input_capacity())?
                .ok_or_else(|| {
                    MarketError::SourceUnavailable(format!(
                        "managed Market connection is missing: {}",
                        plan.descriptor.id
                    ))
                })?;
            self.attach_source(handle)
                .map_err(MarketError::SourceUnavailable)?;
        }
        self.spawn_source_inputs(context);
        Ok(())
    }
}

impl ConfluxActor for MarketApplication {
    type FatalError = MarketError;
    type LocalEvent = MarketConfluxEvent;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.activate_managed_sources(context)?;
        self.spawn_source_inputs(context);
        if let Some(updates) = self.conflux.universe_updates.take() {
            context.spawn_local_receiver_map("reference-universe", updates, |update| {
                MarketConfluxEvent(MarketConfluxEventKind::Universe(update))
            });
        }
        self.sync_source_subscriptions().await?;
        context.spawn_timer("freshness", self.conflux.freshness_interval);
        self.publish(context).await?;
        Ok(())
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent<Self, Self::LocalEvent>,
        context: &mut Context<'_, Self>,
    ) -> Result<Option<MarketRestResponse>, Self::FatalError> {
        let response = match event {
            ConfluxEvent::Rest(request) => {
                Some(self.handle_rest_idempotent(request, context).await)
            }
            ConfluxEvent::Local(MarketConfluxEvent(MarketConfluxEventKind::Source(input))) => {
                self.apply_source_input(input).await?;
                self.sync_source_subscriptions().await?;
                None
            }
            ConfluxEvent::Local(MarketConfluxEvent(MarketConfluxEventKind::Universe(update))) => {
                self.reconcile_market_universe(update)?;
                self.activate_managed_sources(context)?;
                self.sync_source_subscriptions().await?;
                self.spawn_source_inputs(context);
                None
            }
            ConfluxEvent::System(SystemEvent::Timer {
                name,
                fired_at_unix_nanos,
            }) if name == "freshness" => {
                let max_age = self
                    .conflux
                    .freshness_max_age
                    .as_nanos()
                    .min(u128::from(u64::MAX)) as u64;
                self.evaluate_freshness(fired_at_unix_nanos, max_age);
                None
            }
            ConfluxEvent::System(SystemEvent::SourceFailed { source, error }) => {
                tracing::warn!(component = "market", %source, %error, "Market Conflux source stopped");
                None
            }
            _ => None,
        };
        self.publish(context).await?;
        Ok(response)
    }

    async fn stopping(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.shutdown_sources(self.conflux.shutdown_timeout)
            .await
            .map_err(MarketError::ShutdownIncomplete)?;
        self.publish(context).await?;
        if let Some(history) = self.conflux.history.as_mut() {
            history
                .shutdown()
                .await
                .map_err(MarketError::ShutdownIncomplete)?;
        }
        Ok(())
    }
}

impl MarketApplication {
    async fn handle_rest_idempotent(
        &mut self,
        request: MarketRestRequest,
        context: &mut Context<'_, Self>,
    ) -> MarketRestResponse {
        const MAX_COMMAND_RESULTS: usize = 4_096;
        let key = command_key(&request).map(str::to_owned);
        if let Some(key) = key.as_deref() {
            if let Some((cached_request, cached_response)) = self.conflux.command_results.get(key) {
                return if cached_request == &request {
                    cached_response.clone()
                } else {
                    idempotency_conflict(&request)
                };
            }
        }
        let response = self.handle_rest(request.clone(), context).await;
        if let Some(key) = key {
            self.conflux
                .command_results
                .insert(key, (request, response.clone()));
            while self.conflux.command_results.len() > MAX_COMMAND_RESULTS {
                let Some(oldest) = self.conflux.command_results.keys().next().cloned() else {
                    break;
                };
                self.conflux.command_results.remove(&oldest);
            }
        }
        response
    }

    async fn handle_rest(
        &mut self,
        request: MarketRestRequest,
        context: &mut Context<'_, Self>,
    ) -> MarketRestResponse {
        match request {
            MarketRestRequest::Health => MarketRestResponse::Health(Ok(self.contract_health())),
            MarketRestRequest::DataSources(_) => {
                let view = self.current_view();
                MarketRestResponse::DataSources(Ok(MarketDataSourcesResponse {
                    sources: view
                        .sources
                        .values()
                        .map(|source| MarketDataSource {
                            source_id: source.descriptor.id.to_string(),
                            status: format!("{:?}", source.status).to_ascii_lowercase(),
                            ready: source.status == crate::SourceStatus::Ready,
                            stale: source.status == crate::SourceStatus::Degraded,
                        })
                        .collect(),
                }))
            }
            MarketRestRequest::Subscribe(command) => {
                let result = self
                    .subscribe_contract(command)
                    .and_then(|response| Ok::<_, MarketError>(response));
                if result.is_ok() {
                    if let Err(error) = self.activate_managed_sources(context) {
                        return MarketRestResponse::Subscribe(Err(control_error(error)));
                    }
                    if let Err(error) = self.sync_source_subscriptions().await {
                        return MarketRestResponse::Subscribe(Err(control_error(error)));
                    }
                    self.spawn_source_inputs(context);
                }
                MarketRestResponse::Subscribe(result.map_err(control_error))
            }
            MarketRestRequest::Unsubscribe(command) => {
                let owner = strategy_subscription_owner(
                    command.launch_id.as_deref(),
                    &command.instance_id,
                    &command.strategy_id,
                );
                let result = SubscriptionId::new(command.payload.subscription_id)
                    .map_err(MarketError::InvalidSubscription)
                    .and_then(|id| self.unsubscribe_owned(&id, &owner))
                    .and_then(|removed| {
                        removed
                            .then_some(MarketCommandStatus {
                                status: "completed".into(),
                            })
                            .ok_or_else(|| MarketError::NotFound("subscription not found".into()))
                    });
                if result.is_ok() {
                    if let Err(error) = self.sync_source_subscriptions().await {
                        return MarketRestResponse::Unsubscribe(Err(control_error(error)));
                    }
                }
                MarketRestResponse::Unsubscribe(result.map_err(control_error))
            }
            MarketRestRequest::ReleaseOwner(command) => {
                let owner = strategy_subscription_owner(
                    command.launch_id.as_deref(),
                    &command.instance_id,
                    &command.strategy_id,
                );
                let removed = self.release_subscription_owner(&owner);
                let result =
                    self.sync_source_subscriptions()
                        .await
                        .map(|()| MarketReleaseOwnerResponse {
                            released_subscriptions: removed
                                .into_iter()
                                .map(|value| value.0)
                                .collect(),
                        });
                MarketRestResponse::ReleaseOwner(result.map_err(control_error))
            }
            MarketRestRequest::Recover => MarketRestResponse::Recover(
                self.recover_sources()
                    .await
                    .map(|()| MarketCommandStatus {
                        status: "accepted".into(),
                    })
                    .map_err(control_error),
            ),
            MarketRestRequest::PauseReplay => MarketRestResponse::PauseReplay(
                self.set_replay_paused(true)
                    .await
                    .map(|()| MarketCommandStatus {
                        status: "paused".into(),
                    })
                    .map_err(control_error),
            ),
            MarketRestRequest::ResumeReplay => MarketRestResponse::ResumeReplay(
                self.set_replay_paused(false)
                    .await
                    .map(|()| MarketCommandStatus {
                        status: "running".into(),
                    })
                    .map_err(control_error),
            ),
        }
    }

    fn subscribe_contract(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<
            kairos_market_contract::MarketSubscribePayload,
        >,
    ) -> Result<MarketSubscriptionResponse, MarketError> {
        if !matches!(command.schema_version, 1 | 2)
            || command.operation != "market.subscribe"
            || command.command_id.trim().is_empty()
            || command.idempotency_key.trim().is_empty()
            || command.strategy_id.trim().is_empty()
            || command.instance_id.trim().is_empty()
            || command.payload.subject.trim().is_empty()
        {
            return Err(MarketError::Invalid(
                "invalid Market subscribe envelope".into(),
            ));
        }
        let selectors = command
            .payload
            .selectors
            .iter()
            .map(|value| ObservationSelector::parse(value))
            .collect::<Result<Vec<_>, _>>()
            .map_err(MarketError::InvalidSubscription)?;
        let subscription_id = SubscriptionId::new(command.command_id.clone())
            .map_err(MarketError::InvalidSubscription)?;
        let owner = strategy_subscription_owner(
            command.launch_id.as_deref(),
            &command.instance_id,
            &command.strategy_id,
        );
        let exchange = command.payload.exchange.as_deref().unwrap_or("binance");
        let market_type = command.payload.market_type.as_deref().unwrap_or("spot");
        let subject = command
            .payload
            .subject
            .strip_prefix("market.")
            .unwrap_or(&command.payload.subject);
        let chain = command
            .payload
            .params
            .get("mode")
            .and_then(serde_json::Value::as_str)
            .is_some_and(|value| value.eq_ignore_ascii_case("chain"));
        if command.payload.dynamic && !chain {
            return Err(MarketError::Unsupported(
                "dynamic subscriptions require params.mode=chain".into(),
            ));
        }
        if chain {
            let underlying = command
                .payload
                .params
                .get("underlying")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| {
                    MarketError::Invalid("chain subscription requires underlying".into())
                })?;
            let markets = resolve_option_markets(
                &self.market_universe(),
                exchange,
                command.payload.asset_type.as_deref(),
                underlying,
            )
            .map_err(MarketError::InvalidSubscription)?;
            if markets.is_empty() {
                return Err(MarketError::NotFound(format!(
                    "no option markets for {underlying}"
                )));
            }
            let query = crate::MarketSelectionQuery {
                exchange_id: Some(
                    kairos_primitives::Exchange::new(exchange)
                        .map_err(|error| MarketError::Invalid(error.to_string()))?,
                ),
                provider_product: Some(
                    kairos_primitives::ProviderProductCode::new(market_type)
                        .map_err(|error| MarketError::Invalid(error.to_string()))?,
                ),
                source_id: command
                    .payload
                    .source_id
                    .as_deref()
                    .map(crate::SourceId::new)
                    .transpose()
                    .map_err(MarketError::Invalid)?,
                active_only: true,
                ..Default::default()
            };
            self.subscribe_dynamic_with_selectors(
                subscription_id.clone(),
                owner.clone(),
                query,
                markets,
                selectors,
            )?;
        } else {
            let mut market = resolve_market(
                &self.market_universe(),
                exchange,
                market_type,
                command.payload.asset_type.as_deref(),
                subject,
            )
            .map_err(MarketError::InvalidSubscription)?;
            if let Some(source) = command.payload.source_id.as_deref() {
                market = market
                    .with_source(source)
                    .map_err(MarketError::InvalidSubscription)?;
            }
            self.subscribe_static_with_selectors(
                subscription_id.clone(),
                owner.clone(),
                market,
                selectors,
            )?;
        }
        Ok(MarketSubscriptionResponse {
            subscription_id: subscription_id.0.clone(),
            owner_id: owner,
            status: self
                .subscription_status(&subscription_id)
                .map(|value| format!("{value:?}").to_ascii_lowercase())
                .unwrap_or_else(|| "pending".into()),
        })
    }

    fn contract_health(&self) -> MarketHealthResponse {
        let view = self.current_view();
        let feed_status = format!("{:?}", view.feed_status).to_ascii_lowercase();
        MarketHealthResponse {
            status: if matches!(view.feed_status, crate::FeedStatus::Degraded) {
                "degraded"
            } else {
                "ready"
            }
            .into(),
            actor_id: view.actor_id.to_string(),
            event_sequence: self.event_sequence(),
            feed_status,
        }
    }

    async fn publish(&mut self, context: &mut Context<'_, Self>) -> Result<(), MarketError> {
        let changes = self.drain_changes_limited(1_024);
        let events = changes
            .iter()
            .filter_map(|change| {
                change
                    .event
                    .clone()
                    .map(|event| (change.sequence.get(), event))
            })
            .collect::<Vec<_>>();
        if let Some(history) = self.conflux.history.as_ref() {
            history
                .record(&events)
                .await
                .map_err(MarketError::Recovery)?;
        }
        let actor_id = self.current_view().actor_id.to_string();
        let event_key = "market-events".to_owned();
        if let Some(publisher) = context.system().aeron_publishers.get_mut(&event_key) {
            for (sequence, event) in &events {
                let bytes = encode_event(&actor_id, &self.conflux.identity, *sequence, event)
                    .map_err(MarketError::Recovery)?;
                publisher
                    .resource_mut()
                    .publish(&bytes)
                    .map_err(|error| MarketError::Recovery(error.to_string()))?;
            }
        }
        for change in &changes {
            let Some(encoded) = encode_change_view(&actor_id, &self.conflux.identity, change)
                .map_err(MarketError::Recovery)?
            else {
                continue;
            };
            let resource_key = encoded.key.canonical_key();
            if context
                .system()
                .market_view_publishers
                .get(&resource_key)
                .is_none()
            {
                let writer = kairos_market_contract::MarketViewPublisher::create(
                    &self.conflux.view_root,
                    encoded.key,
                    self.conflux.view_slot_size,
                )
                .map_err(|error| MarketError::Recovery(error.to_string()))?;
                context
                    .system()
                    .market_view_publishers
                    .ensure_with(resource_key.clone(), 1, || writer)
                    .map_err(|error| MarketError::Recovery(error.to_string()))?;
            }
            context
                .system()
                .market_view_publishers
                .get_mut(&resource_key)
                .expect("Market view publisher was inserted")
                .resource_mut()
                .publish(
                    SnapshotEnvelopeMetadata {
                        resource_epoch: 1,
                        producer_incarnation: self.conflux.producer_incarnation,
                        generation: change.sequence.get(),
                        applied_event_sequence: change.sequence.get(),
                        published_at_unix_nanos: now_unix_nanos(),
                    },
                    &encoded.bytes,
                )
                .map_err(|error| MarketError::Recovery(error.to_string()))?;
        }
        Ok(())
    }
}

fn command_key(request: &MarketRestRequest) -> Option<&str> {
    match request {
        MarketRestRequest::Subscribe(command) => Some(command.idempotency_key.as_str()),
        MarketRestRequest::Unsubscribe(command) => Some(command.idempotency_key.as_str()),
        MarketRestRequest::ReleaseOwner(command) => Some(command.idempotency_key.as_str()),
        _ => None,
    }
    .filter(|key| !key.trim().is_empty())
}

fn idempotency_conflict(request: &MarketRestRequest) -> MarketRestResponse {
    let error = MarketControlError {
        code: "command.idempotency_conflict".into(),
        message: "idempotency key was already used with a different request".into(),
        retryable: false,
        details: BTreeMap::new(),
    };
    match request {
        MarketRestRequest::Subscribe(_) => MarketRestResponse::Subscribe(Err(error)),
        MarketRestRequest::Unsubscribe(_) => MarketRestResponse::Unsubscribe(Err(error)),
        MarketRestRequest::ReleaseOwner(_) => MarketRestResponse::ReleaseOwner(Err(error)),
        _ => unreachable!("only commands with idempotency keys can conflict"),
    }
}

fn take_managed_source(
    system: &mut kairos_conflux::ConfluxSystem,
    plan: &MarketSourcePlan,
    input_capacity: usize,
) -> Result<Option<SourceHandle>, MarketError> {
    let key = plan.descriptor.id.to_string();
    macro_rules! snapshot {
        ($connections:expr, $interval:expr) => {
            if let Some(connection) = $connections.remove(&key) {
                return Ok(Some(spawn_snapshot(
                    plan.descriptor.clone(),
                    connection.into_connection(),
                    $interval,
                    input_capacity,
                )));
            }
        };
    }
    macro_rules! stream {
        ($connections:expr, $scoped:expr) => {
            if let Some(connection) = $connections.remove(&key) {
                let connection = connection.into_connection();
                return Ok(Some(if $scoped {
                    spawn_stream_with_policy(
                        plan.descriptor.clone(),
                        connection,
                        input_capacity,
                        StreamFailurePolicy::MarketScopedResync,
                    )
                } else {
                    spawn_stream(plan.descriptor.clone(), connection, input_capacity)
                }));
            }
        };
    }
    match plan.mode {
        MarketSourceMode::Snapshot(interval) => {
            snapshot!(system.binance_spot_rest_connections, interval);
            snapshot!(system.binance_usdm_rest_connections, interval);
            snapshot!(system.binance_coinm_rest_connections, interval);
            snapshot!(system.binance_options_rest_connections, interval);
            snapshot!(system.binance_stocks_rest_connections, interval);
            snapshot!(system.okx_public_rest_connections, interval);
            snapshot!(system.hyperliquid_info_rest_connections, interval);
            snapshot!(system.ibkr_market_data_connections, interval);
        }
        MarketSourceMode::Stream | MarketSourceMode::MarketScopedStream => {
            let scoped = matches!(plan.mode, MarketSourceMode::MarketScopedStream);
            stream!(system.binance_spot_websocket_connections, scoped);
            stream!(system.binance_usdm_websocket_connections, scoped);
            stream!(system.binance_coinm_websocket_connections, scoped);
            stream!(system.binance_options_websocket_connections, scoped);
            stream!(system.binance_stocks_websocket_connections, scoped);
            stream!(system.okx_public_websocket_connections, scoped);
            stream!(system.hyperliquid_websocket_connections, scoped);
            stream!(system.massive_stocks_websocket_connections, scoped);
            stream!(system.massive_options_websocket_connections, scoped);
        }
    }
    Ok(None)
}

fn control_error(error: MarketError) -> MarketControlError {
    MarketControlError {
        code: "market.request_failed".into(),
        message: error.to_string(),
        retryable: matches!(
            error,
            MarketError::SourceUnavailable(_)
                | MarketError::QueueOverflow(_)
                | MarketError::Recovery(_)
        ),
        details: Default::default(),
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}
