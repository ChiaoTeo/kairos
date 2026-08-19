use std::collections::BTreeMap;
use std::time::Duration;

use kairos_conflux::{
    ConfluxActor, ConfluxEvent, ConnectionKey, Context, Contract, ExternalParticipantEvent,
    IntegrationError, MarketQuoteQuery, MarketSubscriptionCommand, ResourceOperationError,
    RestContract, SystemEvent,
};
use kairos_market_contract::{
    MarketCommandStatus, MarketControlError, MarketDataSource, MarketDataSourcesResponse,
    MarketHealthResponse, MarketReleaseOwnerResponse, MarketRestRequest, MarketRestResponse,
    MarketSubscriptionResponse,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_transport::SnapshotEnvelopeMetadata;

use super::{MarketApplication, MarketError, resolve_market, resolve_option_markets};
use crate::domain::source::{
    SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceRouteKey, SourceStatus,
};
use crate::services::actor::BusinessSubscriptionKey;
use crate::services::publication::HistoryQueue;
use crate::services::publication::contract::{encode_change_view, encode_event};
use crate::services::source::messages::{ProviderSubscriptionId, SourceInput};
use crate::services::source::{
    confirmed_subscription, confirmed_unsubscription, normalize, quote_event, subscription_request,
    with_epoch,
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
}

pub(crate) struct ReferenceProjectionConfig {
    pub(crate) client_key: String,
    pub(crate) interval: Duration,
    pub(crate) projection: crate::services::reference_projection::ReferenceUniverseProjection,
}

struct ReferenceProjectionState {
    config: ReferenceProjectionConfig,
    required_sequence: u64,
    published_sequence: Option<u64>,
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
    reference_projection: Option<ReferenceProjectionState>,
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
            reference_projection: None,
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
        reference_projection: Option<ReferenceProjectionConfig>,
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
        self.conflux.reference_projection =
            reference_projection.map(|config| ReferenceProjectionState {
                config,
                required_sequence: 0,
                published_sequence: None,
            });
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
            if !managed_connection_exists(context, plan) {
                return Err(MarketError::SourceUnavailable(format!(
                    "managed Market connection is missing: {}",
                    plan.descriptor.id
                )));
            }
            self.attach_managed_source(plan.descriptor.clone())
                .map_err(MarketError::SourceUnavailable)?;
            if let MarketSourceMode::Snapshot(interval) = plan.mode {
                self.actor
                    .apply_source_status(
                        &plan.descriptor.id,
                        SourceEpoch::new(1),
                        SourceStatus::Ready,
                        None,
                    )
                    .map_err(MarketError::Invalid)?;
                context.spawn_timer(snapshot_timer_name(&plan.descriptor.id), interval);
            }
        }
        self.spawn_source_inputs(context);
        Ok(())
    }

    async fn refresh_reference_universe(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), MarketError> {
        let Some(reference) = self.conflux.reference_projection.as_ref() else {
            return Ok(());
        };
        let client_key = reference.config.client_key.clone();
        let required_sequence = reference.required_sequence;
        let published_sequence = reference.published_sequence;
        let projection = reference.config.projection.clone();
        let snapshot = context
            .reference_client(&client_key)
            .ok_or_else(|| {
                MarketError::SourceUnavailable(format!(
                    "managed Reference client is missing: {client_key}"
                ))
            })?
            .market_snapshot()
            .map_err(|error| MarketError::SourceUnavailable(error.to_string()))?;
        let update = projection
            .project(&snapshot, required_sequence)
            .map_err(MarketError::SourceUnavailable)?;
        if published_sequence.is_some_and(|sequence| sequence >= update.event_sequence.get()) {
            return Ok(());
        }
        if let Some(reference) = self.conflux.reference_projection.as_mut() {
            reference.published_sequence = Some(update.event_sequence.get());
        }
        self.reconcile_market_universe(update)?;
        self.activate_managed_sources(context)?;
        self.sync_all_source_subscriptions(context).await?;
        self.spawn_source_inputs(context);
        Ok(())
    }
}

fn reference_event_sequence(event: &kairos_reference_contract::ReferenceEvent<'_>) -> u64 {
    use kairos_reference_contract::ReferenceEvent;
    match event {
        ReferenceEvent::EntityUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::EntityUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::AssetUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::AssetUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::InstrumentUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::InstrumentUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::ListingUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::ListingUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::MarketUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::MarketUpdated(value) => value.metadata().sequence(),
    }
}

impl ConfluxActor for MarketApplication {
    type FatalError = MarketError;
    type LocalEvent = MarketConfluxEvent;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.activate_managed_sources(context)?;
        self.spawn_source_inputs(context);
        if let Some(reference) = self.conflux.reference_projection.as_ref() {
            context.spawn_timer("reference-universe", reference.config.interval);
        }
        self.sync_all_source_subscriptions(context).await?;
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
            },
            ConfluxEvent::Local(MarketConfluxEvent(MarketConfluxEventKind::Source(input))) => {
                self.apply_source_input(input).await?;
                self.sync_all_source_subscriptions(context).await?;
                None
            },
            ConfluxEvent::Reference(reference) => {
                if self
                    .conflux
                    .reference_projection
                    .as_ref()
                    .is_some_and(|state| state.config.client_key == reference.client)
                {
                    if let Ok(event) = reference.frame.decode() {
                        let sequence = reference_event_sequence(&event);
                        if let Some(state) = self.conflux.reference_projection.as_mut() {
                            state.required_sequence = state.required_sequence.max(sequence);
                        }
                    }
                    self.refresh_reference_universe(context).await?;
                }
                None
            },
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
            },
            ConfluxEvent::System(SystemEvent::Timer { name, .. })
                if name.starts_with("market-snapshot:") =>
            {
                self.poll_managed_snapshot(&name["market-snapshot:".len()..], context)
                    .await?;
                None
            },
            ConfluxEvent::System(SystemEvent::Timer { name, .. })
                if name == "reference-universe" =>
            {
                self.refresh_reference_universe(context).await?;
                None
            },
            ConfluxEvent::System(SystemEvent::SourceReady { source }) => {
                if let Some(source_id) = managed_source_id_from_system_event(&source) {
                    self.mark_managed_source_ready(source_id)?;
                    self.sync_all_source_subscriptions(context).await?;
                }
                None
            },
            ConfluxEvent::Integration(integration) => {
                if let ExternalParticipantEvent::Market(event) = integration.event {
                    self.apply_managed_market_event(
                        integration.identity.descriptor.connection_key.as_str(),
                        integration.identity.generation,
                        event,
                    )
                    .await?;
                }
                None
            },
            ConfluxEvent::System(SystemEvent::SourceFailed { source, error }) => {
                tracing::warn!(component = "market", %source, %error, "Market Conflux source stopped");
                None
            },
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
                            source_id: source.descriptor.id.clone(),
                            status: format!("{:?}", source.status).to_ascii_lowercase(),
                            ready: source.status == crate::SourceStatus::Ready,
                            stale: source.status == crate::SourceStatus::Degraded,
                        })
                        .collect(),
                }))
            },
            MarketRestRequest::Subscribe(command) => {
                let result = self
                    .subscribe_contract(command)
                    .and_then(|response| Ok::<_, MarketError>(response));
                if result.is_ok() {
                    if let Err(error) = self.activate_managed_sources(context) {
                        return MarketRestResponse::Subscribe(Err(control_error(error)));
                    }
                    if let Err(error) = self.sync_all_source_subscriptions(context).await {
                        return MarketRestResponse::Subscribe(Err(control_error(error)));
                    }
                    self.spawn_source_inputs(context);
                }
                MarketRestResponse::Subscribe(result.map_err(control_error))
            },
            MarketRestRequest::Unsubscribe(command) => {
                let owner = strategy_subscription_owner(
                    command.launch_id.as_deref(),
                    &command.instance_id,
                    &command.strategy_id,
                );
                let result = self
                    .unsubscribe_owned(&command.payload.subscription_id, &owner)
                    .and_then(|removed| {
                        removed
                            .then_some(MarketCommandStatus {
                                status: "completed".into(),
                            })
                            .ok_or_else(|| MarketError::NotFound("subscription not found".into()))
                    });
                if result.is_ok() {
                    if let Err(error) = self.sync_all_source_subscriptions(context).await {
                        return MarketRestResponse::Unsubscribe(Err(control_error(error)));
                    }
                }
                MarketRestResponse::Unsubscribe(result.map_err(control_error))
            },
            MarketRestRequest::ReleaseOwner(command) => {
                let owner = strategy_subscription_owner(
                    command.launch_id.as_deref(),
                    &command.instance_id,
                    &command.strategy_id,
                );
                let removed = self.release_subscription_owner(&owner);
                let result = self.sync_all_source_subscriptions(context).await.map(|()| {
                    MarketReleaseOwnerResponse {
                        released_subscriptions: removed,
                    }
                });
                MarketRestResponse::ReleaseOwner(result.map_err(control_error))
            },
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
            .map_err(|error| MarketError::InvalidSubscription(error.to_string()))?;
        let owner = strategy_subscription_owner(
            command.launch_id.as_deref(),
            &command.instance_id,
            &command.strategy_id,
        );
        let exchange = command
            .payload
            .exchange
            .as_ref()
            .map(|value| value.as_str())
            .unwrap_or("binance");
        let market_type = command
            .payload
            .market_type
            .map(|value| value.as_str())
            .unwrap_or("spot");
        let asset_type = command.payload.asset_type.map(|value| value.as_str());
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
            let markets =
                resolve_option_markets(&self.market_universe(), exchange, asset_type, underlying)
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
                source_id: command.payload.source_id.clone(),
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
                asset_type,
                subject,
            )
            .map_err(MarketError::InvalidSubscription)?;
            if let Some(source) = command.payload.source_id.as_ref() {
                market = market
                    .with_source(source.as_str())
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
            subscription_id: subscription_id.clone(),
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
        for (sequence, event) in &events {
            let bytes = encode_event(&actor_id, &self.conflux.identity, *sequence, event)
                .map_err(MarketError::Recovery)?;
            match context
                .system()
                .market_event_publishers
                .try_with(&event_key, |publisher| publisher.publish(&bytes))
            {
                Ok(()) | Err(ResourceOperationError::NotFound) => {},
                Err(ResourceOperationError::Operation(error)) => {
                    return Err(MarketError::Recovery(error.to_string()));
                },
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
                .try_with(&resource_key, |publisher| {
                    publisher.publish(
                        SnapshotEnvelopeMetadata {
                            resource_epoch: 1,
                            producer_incarnation: self.conflux.producer_incarnation,
                            generation: change.sequence.get(),
                            applied_event_sequence: change.sequence.get(),
                            published_at_unix_nanos: now_unix_nanos(),
                        },
                        &encoded.bytes,
                    )
                })
                .map_err(|error| match error {
                    ResourceOperationError::NotFound => {
                        MarketError::Recovery("Market view publisher disappeared".into())
                    },
                    ResourceOperationError::Operation(error) => {
                        MarketError::Recovery(error.to_string())
                    },
                })?;
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

fn snapshot_timer_name(source_id: &SourceId) -> String {
    format!("market-snapshot:{source_id}")
}

fn managed_source_id_from_system_event(source: &str) -> Option<&str> {
    source.strip_prefix("integration:")
}

fn managed_connection_exists(
    context: &mut Context<'_, MarketApplication>,
    plan: &MarketSourcePlan,
) -> bool {
    let key = ConnectionKey::new(plan.descriptor.id.to_string()).expect("valid source id");
    let connections = context.connections();
    let contains = |keys: Vec<ConnectionKey>| keys.contains(&key);
    match plan.mode {
        MarketSourceMode::Snapshot(_) => {
            contains(connections.binance_spot_rest.keys())
                || contains(connections.binance_usdm_rest.keys())
                || contains(connections.binance_coinm_rest.keys())
                || contains(connections.binance_options_rest.keys())
                || contains(connections.binance_stocks_rest.keys())
                || contains(connections.okx_public_rest.keys())
                || contains(connections.hyperliquid_info_rest.keys())
                || contains(connections.ibkr_market_data.keys())
        },
        MarketSourceMode::Stream | MarketSourceMode::MarketScopedStream => {
            contains(connections.binance_spot_websocket.keys())
                || contains(connections.binance_usdm_websocket.keys())
                || contains(connections.binance_coinm_websocket.keys())
                || contains(connections.binance_options_websocket.keys())
                || contains(connections.binance_stocks_websocket.keys())
                || contains(connections.okx_public_websocket.keys())
                || contains(connections.hyperliquid_websocket.keys())
                || contains(connections.massive_stocks_websocket.keys())
                || contains(connections.massive_options_websocket.keys())
        },
    }
}

impl MarketApplication {
    async fn sync_all_source_subscriptions(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), MarketError> {
        self.sync_source_subscriptions().await?;
        self.sync_managed_source_subscriptions(context).await
    }

    fn desired_managed_markets(
        &self,
        source_id: &SourceId,
    ) -> BTreeMap<BusinessSubscriptionKey, crate::ResolvedMarket> {
        let Some(source) = self.actor.attached_sources.get(source_id) else {
            return BTreeMap::new();
        };
        let mut desired = BTreeMap::new();
        for subscription in self.current_view().subscriptions {
            if !super::sources::source_supports_selectors(
                &source.descriptor,
                &subscription.selectors,
            ) {
                continue;
            }
            for (market_key, market) in subscription.members {
                if super::source_accepts(&source.descriptor, &market) {
                    desired.insert((subscription.id.clone(), market_key), market);
                }
            }
        }
        desired
    }

    async fn sync_managed_source_subscriptions(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), MarketError> {
        let source_ids = self
            .actor
            .attached_sources
            .iter()
            .filter(|(_, source)| source.inputs.is_none() && source.task.is_none())
            .map(|(id, _)| id.clone())
            .collect::<Vec<_>>();
        for source_id in source_ids {
            let wanted = self.desired_managed_markets(&source_id);
            let confirmed = self.actor.attached_sources[&source_id].confirmed.clone();
            let plan = self
                .conflux
                .source_plans
                .get(source_id.as_str())
                .expect("attached managed source has a plan")
                .clone();
            for (key, handle) in confirmed
                .iter()
                .filter(|(key, _)| !wanted.contains_key(*key))
            {
                if !matches!(plan.mode, MarketSourceMode::Snapshot(_)) {
                    managed_unsubscribe(
                        context,
                        &ConnectionKey::new(source_id.to_string()).expect("valid source id"),
                        handle,
                    )
                    .await
                    .map_err(|error| MarketError::SourceUnavailable(error.to_string()))?;
                }
                self.actor
                    .attached_sources
                    .get_mut(&source_id)
                    .expect("source remains attached")
                    .confirmed
                    .remove(key);
            }
            for (key, market) in wanted {
                if confirmed.contains_key(&key) {
                    continue;
                }
                let handle = if matches!(plan.mode, MarketSourceMode::Snapshot(_)) {
                    ProviderSubscriptionId::new(format!(
                        "snapshot:{}:{}",
                        source_id,
                        self.actor.attached_sources[&source_id].confirmed.len() + 1
                    ))
                    .map_err(MarketError::Invalid)?
                } else {
                    let request = subscription_request(&market)
                        .map_err(|error| MarketError::SourceUnavailable(error.to_string()))?;
                    let id = match managed_subscribe(
                        context,
                        &ConnectionKey::new(source_id.to_string()).expect("valid source id"),
                        request,
                    )
                    .await
                    {
                        Ok(id) => id,
                        Err(IntegrationError::NotReady) => continue,
                        Err(error) => {
                            return Err(MarketError::SourceUnavailable(error.to_string()));
                        },
                    };
                    ProviderSubscriptionId::new(id.0.to_string()).map_err(MarketError::Invalid)?
                };
                self.actor
                    .attached_sources
                    .get_mut(&source_id)
                    .expect("source remains attached")
                    .confirmed
                    .insert(key, handle);
            }
        }
        Ok(())
    }

    fn mark_managed_source_ready(&mut self, value: &str) -> Result<(), MarketError> {
        let Ok(source_id) = SourceId::new(value) else {
            return Ok(());
        };
        if self
            .actor
            .attached_sources
            .get(&source_id)
            .is_some_and(|source| source.inputs.is_none() && source.task.is_none())
        {
            self.actor
                .apply_source_status(&source_id, SourceEpoch::new(1), SourceStatus::Ready, None)
                .map_err(MarketError::Invalid)?;
        }
        Ok(())
    }

    async fn apply_managed_market_event(
        &mut self,
        connection_key: &str,
        generation: u64,
        event: kairos_conflux::MarketEvent,
    ) -> Result<(), MarketError> {
        let source_id = SourceId::new(connection_key)
            .map_err(|error| MarketError::Invalid(error.to_string()))?;
        if !self.actor.attached_sources.contains_key(&source_id) {
            return Ok(());
        }
        let mut markets = self
            .desired_managed_markets(&source_id)
            .into_values()
            .filter(|market| {
                market
                    .route
                    .provider_symbol
                    .eq_ignore_ascii_case(event.symbol.as_str())
            })
            .map(|market| (SourceRouteKey::from_market(&market), market))
            .collect::<BTreeMap<_, _>>()
            .into_values();
        let Some(market) = markets.next() else {
            return Ok(());
        };
        if let Some(input) = normalize(&source_id, &market, event)
            .map_err(MarketError::Invalid)?
            .map(|value| with_epoch(value, source_id, SourceEpoch::new(generation.max(1))))
        {
            self.apply_source_input(input).await?;
        }
        Ok(())
    }

    async fn poll_managed_snapshot(
        &mut self,
        value: &str,
        context: &mut Context<'_, Self>,
    ) -> Result<(), MarketError> {
        let source_id =
            SourceId::new(value).map_err(|error| MarketError::Invalid(error.to_string()))?;
        let markets = self
            .desired_managed_markets(&source_id)
            .into_values()
            .map(|market| (SourceRouteKey::from_market(&market), market))
            .collect::<BTreeMap<_, _>>();
        if markets.is_empty() {
            return Ok(());
        }
        let symbols = markets
            .values()
            .map(|market| {
                kairos_primitives::ParticipantSymbol::new(market.route.provider_symbol.as_str())
                    .expect("resolved provider symbol is valid")
            })
            .collect::<Vec<_>>();
        match managed_fetch_quotes(
            context,
            &ConnectionKey::new(source_id.to_string()).expect("valid source id"),
            &symbols,
        )
        .await
        {
            Ok(quotes) => {
                for quote in quotes {
                    self.apply_managed_market_event(source_id.as_str(), 1, quote_event(quote))
                        .await?;
                }
            },
            Err(error) => {
                self.actor
                    .apply_source_failure(
                        &source_id,
                        SourceEpoch::new(1),
                        SourceFailureKind::Transport,
                        error.to_string(),
                    )
                    .map_err(MarketError::Invalid)?;
            },
        }
        Ok(())
    }
}

async fn managed_subscribe(
    context: &mut Context<'_, MarketApplication>,
    key: &ConnectionKey,
    request: kairos_conflux::MarketSubscriptionRequest,
) -> Result<kairos_conflux::MarketSubscriptionId, IntegrationError> {
    macro_rules! try_family {
        ($field:ident) => {{
            let mut connections = context.connections();
            if connections.$field.keys().contains(key) {
                let connection = connections
                    .$field
                    .get(key)
                    .map_err(|_| IntegrationError::NotReady)?;
                return confirmed_subscription(connection.subscribe(request).await?);
            }
        }};
    }
    try_family!(binance_spot_websocket);
    try_family!(binance_usdm_websocket);
    try_family!(binance_coinm_websocket);
    try_family!(binance_options_websocket);
    try_family!(binance_stocks_websocket);
    try_family!(okx_public_websocket);
    try_family!(hyperliquid_websocket);
    try_family!(massive_stocks_websocket);
    try_family!(massive_options_websocket);
    Err(IntegrationError::Unavailable(format!(
        "managed Market stream connection is missing: {key}"
    )))
}

async fn managed_unsubscribe(
    context: &mut Context<'_, MarketApplication>,
    key: &ConnectionKey,
    handle: &ProviderSubscriptionId,
) -> Result<(), IntegrationError> {
    let subscription = handle
        .as_str()
        .parse::<u64>()
        .map(kairos_conflux::MarketSubscriptionId)
        .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
    macro_rules! try_family {
        ($field:ident) => {{
            let mut connections = context.connections();
            if connections.$field.keys().contains(key) {
                let connection = connections
                    .$field
                    .get(key)
                    .map_err(|_| IntegrationError::NotReady)?;
                return confirmed_unsubscription(connection.unsubscribe(subscription).await?);
            }
        }};
    }
    try_family!(binance_spot_websocket);
    try_family!(binance_usdm_websocket);
    try_family!(binance_coinm_websocket);
    try_family!(binance_options_websocket);
    try_family!(binance_stocks_websocket);
    try_family!(okx_public_websocket);
    try_family!(hyperliquid_websocket);
    try_family!(massive_stocks_websocket);
    try_family!(massive_options_websocket);
    Err(IntegrationError::Unavailable(format!(
        "managed Market stream connection is missing: {key}"
    )))
}

async fn managed_fetch_quotes(
    context: &mut Context<'_, MarketApplication>,
    key: &ConnectionKey,
    symbols: &[kairos_primitives::ParticipantSymbol],
) -> Result<Vec<kairos_conflux::MarketQuote>, IntegrationError> {
    macro_rules! try_family {
        ($field:ident) => {{
            let mut connections = context.connections();
            if let Ok(connection) = connections.$field.get(key) {
                return connection.fetch_quotes(symbols).await;
            }
        }};
    }
    try_family!(binance_spot_rest);
    try_family!(binance_usdm_rest);
    try_family!(binance_coinm_rest);
    try_family!(binance_options_rest);
    try_family!(binance_stocks_rest);
    try_family!(okx_public_rest);
    try_family!(hyperliquid_info_rest);
    try_family!(ibkr_market_data);
    Err(IntegrationError::Unavailable(format!(
        "managed Market quote connection is missing: {key}"
    )))
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
