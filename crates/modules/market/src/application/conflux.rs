use std::collections::BTreeMap;
use std::time::Duration;

use kairos_conflux::{
    ConfluxActor, ConfluxEvent, ConnectionKey, Context, ExternalParticipantEvent, IntegrationError,
    MarketQuoteQuery, MarketSubscriptionCommand, SystemEvent,
};
use kairos_market_contract::{
    MarketCommandOutcome, MarketCommandStatus, MarketControlError, MarketDataRoute,
    MarketDataRouteState, MarketDataRoutesResponse, MarketFeedStatus, MarketHealthResponse,
    MarketHealthStatus, MarketOperation, MarketReleaseOwnerPayload, MarketReleaseOwnerResponse,
    MarketSubscribePayload, MarketSubscriptionResponse, MarketSubscriptionState, MarketTarget,
    MarketUnsubscribePayload, ProviderPreference, SubscriptionOwnerKey, SubscriptionPendingReason,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::control::jsonrpc::{ErrorObjectOwned, RpcResult, business_error};

use super::{MarketApplication, MarketError, MarketRpcActor};
use crate::domain::source::{
    FeedDescriptor, MarketFeedId, SourceEpoch, SourceFailureKind, SourceStatus,
};
use crate::services::actor::PhysicalSubscriptionKey;
use crate::services::publication::HistoryQueue;
use crate::services::publication::contract::{encode_change_view, encode_event};
use crate::services::source::messages::{ProviderSubscriptionId, SourceInput};
use crate::services::source::{
    confirmed_subscription, confirmed_unsubscription, normalize, quote_event, subscription_request,
    with_epoch,
};
use crate::{ObservationSelector, SubscriptionId};

const MARKET_BUSINESS_ERROR_CODE: i32 = -31_006;

pub struct MarketLocalEvent(SourceInput);

fn strategy_subscription_owner(
    launch_id: Option<&str>,
    instance_id: &str,
    strategy_id: &str,
) -> String {
    let launch_id = launch_id.unwrap_or_default();
    format!(
        "strategy|{}:{}|{}:{}|{}:{}",
        launch_id.len(),
        launch_id,
        instance_id.len(),
        instance_id,
        strategy_id.len(),
        strategy_id,
    )
}

#[derive(Clone, Copy)]
pub(crate) enum MarketSourceMode {
    Snapshot(Duration),
    Stream,
    MarketScopedStream,
}

#[derive(Clone)]
pub(crate) struct MarketSourcePlan {
    pub(crate) descriptor: FeedDescriptor,
    pub(crate) mode: MarketSourceMode,
}

pub(crate) struct ReferenceUniverseSyncConfig {
    pub(crate) client_key: String,
    pub(crate) interval: Duration,
    pub(crate) resolver: crate::application::MarketUniverseResolver,
}

struct ReferenceUniverseSyncState {
    config: ReferenceUniverseSyncConfig,
    required_sequence: u64,
    published_sequence: Option<u64>,
}

pub(crate) struct MarketConfluxState {
    freshness_interval: Duration,
    freshness_max_age: Duration,
    shutdown_timeout: Duration,
    identity: InstanceIdentity,
    producer_incarnation: u64,
    source_plans: BTreeMap<String, MarketSourcePlan>,
    history: Option<HistoryQueue>,
    reference_universe_sync: Option<ReferenceUniverseSyncState>,
    command_results: BTreeMap<String, CachedMarketControlResult>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
enum CachedMarketControlRequest {
    Subscribe(kairos_market_contract::MarketCommandEnvelope<MarketSubscribePayload>),
    Unsubscribe(kairos_market_contract::MarketCommandEnvelope<MarketUnsubscribePayload>),
    ReleaseOwner(kairos_market_contract::MarketCommandEnvelope<MarketReleaseOwnerPayload>),
}

#[derive(Clone, Debug)]
enum CachedMarketControlResponse {
    Subscription(MarketSubscriptionResponse),
    Command(MarketCommandStatus),
    ReleaseOwner(MarketReleaseOwnerResponse),
}

struct CachedMarketControlResult {
    request: CachedMarketControlRequest,
    response: CachedMarketControlResponse,
}

impl Default for MarketConfluxState {
    fn default() -> Self {
        Self {
            freshness_interval: Duration::from_secs(1),
            freshness_max_age: Duration::from_secs(5),
            shutdown_timeout: Duration::from_secs(5),
            identity: InstanceIdentity::default(),
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
            source_plans: BTreeMap::new(),
            history: None,
            reference_universe_sync: None,
            command_results: BTreeMap::new(),
        }
    }
}

impl MarketApplication {
    pub(crate) fn indexed_publication_identity(&self) -> (&InstanceIdentity, u64) {
        (&self.conflux.identity, self.conflux.producer_incarnation)
    }

    pub(crate) fn configure_conflux(
        &mut self,
        freshness_interval: Duration,
        freshness_max_age: Duration,
        shutdown_timeout: Duration,
        identity: InstanceIdentity,
        source_plans: Vec<MarketSourcePlan>,
        history: Option<HistoryQueue>,
        reference_universe_sync: Option<ReferenceUniverseSyncConfig>,
    ) -> Result<(), String> {
        if freshness_interval.is_zero() || freshness_max_age.is_zero() {
            return Err("Market Conflux intervals and view slot size must be positive".into());
        }
        self.conflux.freshness_interval = freshness_interval;
        self.conflux.freshness_max_age = freshness_max_age;
        self.conflux.shutdown_timeout = shutdown_timeout;
        self.conflux.identity = identity;
        self.conflux.source_plans = source_plans
            .into_iter()
            .map(|plan| (plan.descriptor.id.to_string(), plan))
            .collect();
        self.conflux.history = history;
        self.conflux.reference_universe_sync =
            reference_universe_sync.map(|config| ReferenceUniverseSyncState {
                config,
                required_sequence: 0,
                published_sequence: None,
            });
        Ok(())
    }

    fn spawn_source_inputs(&mut self, context: &mut Context<'_, Self>) {
        for (_source_id, inputs) in self.take_source_inputs() {
            context.spawn_mapped_local_events(inputs, MarketLocalEvent);
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
            .filter_map(|market| market.data_route().map(|route| (route, market)))
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
            let mut candidates = self
                .conflux
                .source_plans
                .values()
                .filter(|plan| super::source_accepts(&plan.descriptor, &market))
                .cloned()
                .collect::<Vec<_>>();
            candidates.sort_by(|left, right| left.descriptor.id.cmp(&right.descriptor.id));
            let plan = candidates.first().ok_or_else(|| {
                MarketError::SourceUnavailable(format!(
                    "no managed Market connection supports {}",
                    market.scope.key()
                ))
            })?;
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
        let Some(reference) = self.conflux.reference_universe_sync.as_ref() else {
            return Ok(());
        };
        let client_key = reference.config.client_key.clone();
        let required_sequence = reference.required_sequence;
        let published_sequence = reference.published_sequence;
        let resolver = reference.config.resolver.clone();
        let snapshot = context
            .reference_client(&client_key)
            .ok_or_else(|| {
                MarketError::SourceUnavailable(format!(
                    "managed Reference client is missing: {client_key}"
                ))
            })?
            .market_snapshot()
            .map_err(|error| MarketError::SourceUnavailable(error.to_string()))?;
        let update = resolver
            .resolve(&snapshot, required_sequence)
            .map_err(MarketError::SourceUnavailable)?;
        if published_sequence.is_some_and(|sequence| sequence >= update.event_sequence.get()) {
            return Ok(());
        }
        if let Some(reference) = self.conflux.reference_universe_sync.as_mut() {
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
        ReferenceEvent::ExchangeUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::ExchangeUpdated(value) => value.metadata().sequence(),
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
    type LocalEvent = MarketLocalEvent;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.activate_managed_sources(context)?;
        self.spawn_source_inputs(context);
        if let Some(reference) = self.conflux.reference_universe_sync.as_ref() {
            context.spawn_timer("reference-universe", reference.config.interval);
        }
        self.sync_all_source_subscriptions(context).await?;
        context.spawn_timer("freshness", self.conflux.freshness_interval);
        self.publish(context).await?;
        Ok(())
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent<MarketLocalEvent>,
        context: &mut Context<'_, Self>,
    ) -> Result<(), Self::FatalError> {
        match event {
            ConfluxEvent::Local(MarketLocalEvent(input)) => {
                self.apply_source_input(input).await?;
            },
            ConfluxEvent::Reference(reference) => {
                if self
                    .conflux
                    .reference_universe_sync
                    .as_ref()
                    .is_some_and(|state| state.config.client_key == reference.client)
                {
                    if let Ok(event) = reference.frame.decode() {
                        let sequence = reference_event_sequence(&event);
                        if let Some(state) = self.conflux.reference_universe_sync.as_mut() {
                            state.required_sequence = state.required_sequence.max(sequence);
                        }
                    }
                    self.refresh_reference_universe(context).await?;
                }
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
            },
            ConfluxEvent::System(SystemEvent::Timer { name, .. })
                if name.starts_with("market-snapshot:") =>
            {
                self.poll_managed_snapshot(&name["market-snapshot:".len()..], context)
                    .await?;
            },
            ConfluxEvent::System(SystemEvent::Timer { name, .. })
                if name == "reference-universe" =>
            {
                self.refresh_reference_universe(context).await?;
            },
            ConfluxEvent::System(SystemEvent::SourceReady { source }) => {
                if let Some(source_id) = managed_source_id_from_system_event(&source) {
                    self.mark_managed_source_ready(source_id)?;
                    self.sync_all_source_subscriptions(context).await?;
                }
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
            },
            ConfluxEvent::System(SystemEvent::SourceFailed { source, error }) => {
                tracing::warn!(component = "market", %source, %error, "Market Conflux source stopped");
            },
            _ => {},
        };
        self.publish(context).await?;
        Ok(())
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

impl MarketRpcActor for MarketApplication {
    async fn health(
        &mut self,
        (): (),
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketHealthResponse> {
        self.publish(context).await.map_err(rpc_market_error)?;
        Ok(self.contract_health())
    }

    async fn data_routes(
        &mut self,
        query: kairos_market_contract::MarketDataRoutesQuery,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketDataRoutesResponse> {
        let response = self.data_routes_control(query);
        self.publish(context).await.map_err(rpc_market_error)?;
        Ok(response)
    }

    async fn subscribe(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<MarketSubscribePayload>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketSubscriptionResponse> {
        let response = self.subscribe_control(command, context).await?;
        self.publish(context).await.map_err(rpc_market_error)?;
        Ok(response)
    }

    async fn unsubscribe(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<MarketUnsubscribePayload>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketCommandStatus> {
        let response = self.unsubscribe_control(command, context).await?;
        self.publish(context).await.map_err(rpc_market_error)?;
        Ok(response)
    }

    async fn release_owner(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<MarketReleaseOwnerPayload>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketReleaseOwnerResponse> {
        let response = self.release_owner_control(command, context).await?;
        self.publish(context).await.map_err(rpc_market_error)?;
        Ok(response)
    }

    async fn recover(
        &mut self,
        (): (),
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketCommandStatus> {
        self.recover_sources().await.map_err(rpc_market_error)?;
        let response = MarketCommandStatus {
            status: MarketCommandOutcome::Accepted,
        };
        self.publish(context).await.map_err(rpc_market_error)?;
        Ok(response)
    }

    async fn pause_replay(
        &mut self,
        (): (),
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketCommandStatus> {
        self.set_replay_paused(true)
            .await
            .map_err(rpc_market_error)?;
        let response = MarketCommandStatus {
            status: MarketCommandOutcome::Paused,
        };
        self.publish(context).await.map_err(rpc_market_error)?;
        Ok(response)
    }

    async fn resume_replay(
        &mut self,
        (): (),
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketCommandStatus> {
        self.set_replay_paused(false)
            .await
            .map_err(rpc_market_error)?;
        let response = MarketCommandStatus {
            status: MarketCommandOutcome::Running,
        };
        self.publish(context).await.map_err(rpc_market_error)?;
        Ok(response)
    }
}

impl MarketApplication {
    fn data_routes_control(
        &self,
        query: kairos_market_contract::MarketDataRoutesQuery,
    ) -> MarketDataRoutesResponse {
        let mut routes = Vec::new();
        for market in self.market_universe() {
            let Some(market_id) = market.market_id() else {
                continue;
            };
            if query
                .market_id
                .as_ref()
                .is_some_and(|value| value != market_id)
                || query
                    .instrument_id
                    .as_ref()
                    .is_some_and(|value| value != &market.instrument_id)
            {
                continue;
            }
            for route in &market.data_routes {
                if query
                    .provider
                    .as_ref()
                    .is_some_and(|value| value != &route.provider)
                    || query
                        .observation_kind
                        .is_some_and(|kind| !route.observation_kinds.contains(&kind))
                {
                    continue;
                }
                let plan_configured = self
                    .conflux
                    .source_plans
                    .values()
                    .any(|plan| plan.descriptor.provider.as_ref() == Some(&route.provider));
                let states = self
                    .actor
                    .source_states()
                    .filter(|source| source.descriptor.provider.as_ref() == Some(&route.provider))
                    .map(|source| source.status)
                    .collect::<Vec<_>>();
                let configured = plan_configured || !states.is_empty();
                let ready = states.contains(&SourceStatus::Ready);
                if query.configured_only && !configured || query.ready_only && !ready {
                    continue;
                }
                let state = if ready {
                    MarketDataRouteState::Ready
                } else if states.iter().any(|status| {
                    matches!(
                        status,
                        SourceStatus::Degraded
                            | SourceStatus::Reconnecting
                            | SourceStatus::WarmingUp
                    )
                }) {
                    MarketDataRouteState::Degraded
                } else if !states.is_empty() {
                    MarketDataRouteState::Stopped
                } else if plan_configured {
                    MarketDataRouteState::Configured
                } else {
                    MarketDataRouteState::Supported
                };
                routes.push(MarketDataRoute {
                    market_id: market_id.clone(),
                    provider: route.provider.clone(),
                    observation_kinds: route.observation_kinds.iter().copied().collect(),
                    state,
                    selected: market.selected_provider.as_ref() == Some(&route.provider),
                    pending_reason: (!ready).then(|| {
                        if configured {
                            "provider route is configured but not ready"
                        } else {
                            "provider route is not configured"
                        }
                        .to_string()
                    }),
                });
            }
        }
        routes.sort_by(|left, right| {
            left.market_id
                .cmp(&right.market_id)
                .then_with(|| left.provider.cmp(&right.provider))
        });
        MarketDataRoutesResponse { routes }
    }

    fn cached_control_response(
        &self,
        key: &str,
        request: &CachedMarketControlRequest,
    ) -> Option<RpcResult<CachedMarketControlResponse>> {
        self.conflux.command_results.get(key).map(|cached| {
            if cached.request == *request {
                Ok(cached.response.clone())
            } else {
                Err(idempotency_conflict())
            }
        })
    }

    fn remember_control_response(
        &mut self,
        key: String,
        request: CachedMarketControlRequest,
        response: CachedMarketControlResponse,
    ) {
        const MAX_COMMAND_RESULTS: usize = 4_096;
        self.conflux
            .command_results
            .insert(key, CachedMarketControlResult { request, response });
        while self.conflux.command_results.len() > MAX_COMMAND_RESULTS {
            let Some(oldest) = self.conflux.command_results.keys().next().cloned() else {
                break;
            };
            self.conflux.command_results.remove(&oldest);
        }
    }

    async fn subscribe_control(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<MarketSubscribePayload>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketSubscriptionResponse> {
        let key = command.idempotency_key.to_string();
        let request = CachedMarketControlRequest::Subscribe(command.clone());
        if let Some(response) = self.cached_control_response(&key, &request) {
            return match response? {
                CachedMarketControlResponse::Subscription(response) => Ok(response),
                _ => Err(rpc_invalid("cached market response type mismatch")),
            };
        }
        let response = self.subscribe_contract(command).map_err(rpc_market_error)?;
        self.activate_managed_sources(context)
            .map_err(rpc_market_error)?;
        self.sync_all_source_subscriptions(context)
            .await
            .map_err(rpc_market_error)?;
        self.spawn_source_inputs(context);
        self.remember_control_response(
            key,
            request,
            CachedMarketControlResponse::Subscription(response.clone()),
        );
        Ok(response)
    }

    async fn unsubscribe_control(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<MarketUnsubscribePayload>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketCommandStatus> {
        let key = command.idempotency_key.to_string();
        let request = CachedMarketControlRequest::Unsubscribe(command.clone());
        if let Some(response) = self.cached_control_response(&key, &request) {
            return match response? {
                CachedMarketControlResponse::Command(response) => Ok(response),
                _ => Err(rpc_invalid("cached market response type mismatch")),
            };
        }
        let owner = strategy_subscription_owner(
            command.launch_id.as_deref(),
            &command.instance_id,
            &command.strategy_id,
        );
        let removed = self
            .unsubscribe_owned(&command.payload.subscription_id, &owner)
            .map_err(rpc_market_error)?;
        if !removed {
            return Err(rpc_market_error(MarketError::NotFound(
                "subscription not found".into(),
            )));
        }
        self.sync_all_source_subscriptions(context)
            .await
            .map_err(rpc_market_error)?;
        let response = MarketCommandStatus {
            status: MarketCommandOutcome::Applied,
        };
        self.remember_control_response(
            key,
            request,
            CachedMarketControlResponse::Command(response.clone()),
        );
        Ok(response)
    }

    async fn release_owner_control(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<MarketReleaseOwnerPayload>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketReleaseOwnerResponse> {
        let key = command.idempotency_key.to_string();
        let request = CachedMarketControlRequest::ReleaseOwner(command.clone());
        if let Some(response) = self.cached_control_response(&key, &request) {
            return match response? {
                CachedMarketControlResponse::ReleaseOwner(response) => Ok(response),
                _ => Err(rpc_invalid("cached market response type mismatch")),
            };
        }
        let owner = strategy_subscription_owner(
            command.launch_id.as_deref(),
            &command.instance_id,
            &command.strategy_id,
        );
        let removed = self.release_subscription_owner(&owner);
        self.sync_all_source_subscriptions(context)
            .await
            .map_err(rpc_market_error)?;
        let response = MarketReleaseOwnerResponse {
            released_subscriptions: removed,
        };
        self.remember_control_response(
            key,
            request,
            CachedMarketControlResponse::ReleaseOwner(response.clone()),
        );
        Ok(response)
    }

    fn subscribe_contract(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<
            kairos_market_contract::MarketSubscribePayload,
        >,
    ) -> Result<MarketSubscriptionResponse, MarketError> {
        if !matches!(command.schema_version, 1 | 2)
            || command.operation != MarketOperation::Subscribe
            || command.command_id.trim().is_empty()
            || command.idempotency_key.trim().is_empty()
            || command.strategy_id.trim().is_empty()
            || command.instance_id.trim().is_empty()
            || command.payload.observations.is_empty()
        {
            return Err(MarketError::Invalid(
                "invalid Market subscribe envelope".into(),
            ));
        }
        let selectors = command
            .payload
            .observations
            .iter()
            .map(|requirement| ObservationSelector {
                kind: Some(requirement.kind),
                qualifier: requirement.qualifier.clone(),
            })
            .collect::<Vec<_>>();
        let subscription_id = SubscriptionId::new(command.command_id.as_str())
            .map_err(|error| MarketError::InvalidSubscription(error.to_string()))?;
        let owner = strategy_subscription_owner(
            command.launch_id.as_deref(),
            &command.instance_id,
            &command.strategy_id,
        );
        let universe = self.market_universe();
        let (candidates, dynamic_query) =
            resolve_contract_target(&universe, &command.payload.target)?;
        let ready_providers = candidates
            .iter()
            .flat_map(|market| {
                market.data_routes.iter().filter_map(|route| {
                    let provider_ready = self.actor.source_states().any(|source| {
                        source.status == crate::domain::source::SourceStatus::Ready
                            && super::source_accepts(&source.descriptor, market)
                            && super::sources::source_supports_selectors(
                                &source.descriptor,
                                &selectors,
                            )
                            && source.descriptor.provider.as_ref() == Some(&route.provider)
                    });
                    provider_ready.then_some(route.provider.clone())
                })
            })
            .collect::<std::collections::BTreeSet<_>>();
        let markets = select_provider_routes(
            candidates,
            &command.payload.provider_preference,
            &ready_providers,
        )?;
        let resolved_providers = markets
            .iter()
            .filter_map(|market| market.selected_provider.clone())
            .collect::<std::collections::BTreeSet<_>>();

        if let Some(query) = dynamic_query {
            self.subscribe_dynamic_with_selectors(
                subscription_id.clone(),
                owner.clone(),
                query,
                markets,
                selectors,
            )?;
        } else {
            self.subscribe_static_many_with_selectors(
                subscription_id.clone(),
                owner.clone(),
                markets,
                selectors,
            )?;
        }

        let status = self.subscription_status(&subscription_id);
        let active = matches!(status, Some(crate::SubscriptionStatus::Ready));
        let observations = command.payload.observations.clone();
        Ok(MarketSubscriptionResponse {
            subscription_id: subscription_id.clone(),
            owner_id: SubscriptionOwnerKey::new(owner).map_err(MarketError::InvalidSubscription)?,
            state: match status {
                Some(crate::SubscriptionStatus::Ready) => MarketSubscriptionState::Active,
                Some(crate::SubscriptionStatus::Degraded) => MarketSubscriptionState::Degraded,
                Some(crate::SubscriptionStatus::Unavailable) => {
                    MarketSubscriptionState::WaitingForProvider
                },
                Some(crate::SubscriptionStatus::Rejected) => MarketSubscriptionState::Failed,
                Some(crate::SubscriptionStatus::Pending) | None => {
                    MarketSubscriptionState::Resolving
                },
            },
            satisfied: active.then_some(observations.clone()).unwrap_or_default(),
            missing: (!active).then_some(observations).unwrap_or_default(),
            resolved_providers,
            pending_reason: (!active).then_some(SubscriptionPendingReason::ProviderUnavailable {
                required: required_providers(&command.payload.provider_preference),
            }),
        })
    }

    fn contract_health(&self) -> MarketHealthResponse {
        let view = self.current_view();
        let feed_status = match view.feed_status {
            crate::FeedStatus::Disconnected => MarketFeedStatus::Disconnected,
            crate::FeedStatus::Ready => MarketFeedStatus::Ready,
            crate::FeedStatus::Reconnecting => MarketFeedStatus::Reconnecting,
            crate::FeedStatus::WarmingUp => MarketFeedStatus::WarmingUp,
            crate::FeedStatus::Degraded => MarketFeedStatus::Degraded,
        };
        MarketHealthResponse {
            status: if matches!(view.feed_status, crate::FeedStatus::Degraded) {
                MarketHealthStatus::Degraded
            } else {
                MarketHealthStatus::Ready
            },
            actor_id: view.actor_id,
            event_sequence: self.event_sequence().into(),
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
            let bytes = encode_event(
                &actor_id,
                self.conflux.producer_incarnation,
                &self.conflux.identity,
                *sequence,
                event,
            )
            .map_err(MarketError::Recovery)?;
            if context.outputs().aeron.contains(&event_key) {
                context
                    .outputs()
                    .aeron
                    .publish(&event_key, &bytes)
                    .map_err(|error| MarketError::Recovery(error.to_string()))?;
            }
        }
        for change in &changes {
            let Some(encoded) = encode_change_view(change).map_err(MarketError::Recovery)? else {
                continue;
            };
            let mutations = encoded.into_mutations().map_err(MarketError::Recovery)?;
            context
                .outputs()
                .indexed
                .apply(
                    "market-current",
                    &mutations,
                    change.sequence.get(),
                    now_unix_nanos(),
                )
                .map_err(|error| MarketError::Recovery(error.to_string()))?;
        }
        Ok(())
    }
}

fn idempotency_conflict() -> ErrorObjectOwned {
    business_error(
        MARKET_BUSINESS_ERROR_CODE,
        "idempotency key was already used with a different request",
        MarketControlError {
            code: "command.idempotency_conflict".into(),
            message: "idempotency key was already used with a different request".into(),
            retryable: false,
            details: BTreeMap::new(),
        },
    )
}

fn resolve_contract_target(
    universe: &[crate::ResolvedMarket],
    target: &MarketTarget,
) -> Result<
    (
        Vec<crate::ResolvedMarket>,
        Option<crate::MarketSelectionQuery>,
    ),
    MarketError,
> {
    let (mut markets, query) = match target {
        MarketTarget::Market { market_id } => (
            universe
                .iter()
                .filter(|market| market.market_id() == Some(market_id) && market.is_active())
                .cloned()
                .collect::<Vec<_>>(),
            None,
        ),
        MarketTarget::ConsolidatedInstrument {
            instrument_id,
            network_id,
        } => (
            universe
                .iter()
                .filter(|market| {
                    market.instrument_id == *instrument_id
                        && matches!(
                            &market.scope,
                            crate::ObservationScope::Consolidated {
                                network_id: actual,
                                ..
                            } if actual == network_id
                        )
                        && market.is_active()
                })
                .cloned()
                .collect::<Vec<_>>(),
            None,
        ),
        MarketTarget::Options {
            underlying_market_id,
            underlying_instrument_id,
            expiry_from_unix_nanos,
            expiry_to_unix_nanos,
            strike_lower,
            strike_upper,
            option_right,
            limit: _,
            ..
        } => {
            let underlying = underlying_instrument_id.clone().or_else(|| {
                underlying_market_id.as_ref().and_then(|market_id| {
                    universe
                        .iter()
                        .find(|market| market.market_id() == Some(market_id))
                        .map(|market| market.instrument_id.clone())
                })
            });
            let Some(underlying) = underlying else {
                return Err(MarketError::InvalidSubscription(
                    "option target requires a resolvable underlying market or instrument".into(),
                ));
            };
            let markets = universe
                .iter()
                .filter(|market| {
                    market.instrument_kind == kairos_primitives::reference::InstrumentKind::Option
                        && market.underlying_instrument_id.as_ref() == Some(&underlying)
                        && expiry_from_unix_nanos.is_none_or(|value| {
                            market
                                .expiry_unix_nanos
                                .is_some_and(|expiry| expiry >= value)
                        })
                        && expiry_to_unix_nanos.is_none_or(|value| {
                            market
                                .expiry_unix_nanos
                                .is_some_and(|expiry| expiry <= value)
                        })
                        && strike_lower
                            .is_none_or(|value| market.strike.is_some_and(|strike| strike >= value))
                        && strike_upper
                            .is_none_or(|value| market.strike.is_some_and(|strike| strike <= value))
                        && option_right.as_ref().is_none_or(|selected| {
                            market.option_right.as_deref().is_some_and(|actual| {
                                selected.eq_ignore_ascii_case("both")
                                    || actual.eq_ignore_ascii_case(selected)
                                    || (selected.eq_ignore_ascii_case("call")
                                        && actual.eq_ignore_ascii_case("c"))
                                    || (selected.eq_ignore_ascii_case("put")
                                        && actual.eq_ignore_ascii_case("p"))
                            })
                        })
                        && market.is_active()
                })
                .cloned()
                .collect::<Vec<_>>();
            let query = crate::MarketSelectionQuery {
                underlying_instrument_id: Some(underlying),
                expiry_from_unix_nanos: *expiry_from_unix_nanos,
                expiry_to_unix_nanos: *expiry_to_unix_nanos,
                strike_lower: *strike_lower,
                strike_upper: *strike_upper,
                option_right: option_right.clone(),
                active_only: true,
                ..Default::default()
            };
            (markets, Some(query))
        },
    };
    markets.sort_by(|left, right| {
        left.expiry_unix_nanos
            .cmp(&right.expiry_unix_nanos)
            .then_with(|| left.strike.cmp(&right.strike))
            .then_with(|| left.option_right.cmp(&right.option_right))
            .then_with(|| left.member_id().cmp(&right.member_id()))
    });
    if let MarketTarget::Options {
        limit: Some(limit), ..
    } = target
    {
        markets.truncate(*limit as usize);
    }
    if markets.is_empty() {
        return Err(MarketError::NotFound(
            "the selected Market target has not been resolved by Reference".into(),
        ));
    }
    Ok((markets, query))
}

fn select_provider_routes(
    candidates: Vec<crate::ResolvedMarket>,
    preference: &ProviderPreference,
    ready_providers: &std::collections::BTreeSet<kairos_primitives::market::Provider>,
) -> Result<Vec<crate::ResolvedMarket>, MarketError> {
    let mut by_provider = std::collections::BTreeMap::<
        kairos_primitives::market::Provider,
        Vec<crate::ResolvedMarket>,
    >::new();
    for market in candidates {
        for route in &market.data_routes {
            let provider = route.provider.clone();
            let Some(runtime_route) = market.runtime_routes.get(&provider).cloned() else {
                continue;
            };
            let mut selected = market.clone();
            selected
                .data_routes
                .retain(|value| value.provider == provider);
            selected.runtime_routes =
                std::collections::BTreeMap::from([(provider.clone(), runtime_route)]);
            selected.selected_provider = Some(provider.clone());
            by_provider.entry(provider).or_default().push(selected);
        }
    }
    for routes in by_provider.values_mut() {
        routes.sort_by_key(crate::ResolvedMarket::member_id);
    }
    let take_provider = |provider: &kairos_primitives::market::Provider| {
        by_provider.get(provider).cloned().unwrap_or_default()
    };
    let selected: Vec<crate::ResolvedMarket> = match preference {
        ProviderPreference::Automatic => ready_providers
            .iter()
            .find_map(|provider| by_provider.get(provider).cloned())
            .or_else(|| by_provider.values().next().cloned())
            .unwrap_or_default(),
        ProviderPreference::Prefer(preferred) => preferred
            .iter()
            .filter(|provider| ready_providers.contains(*provider))
            .find(|provider| by_provider.contains_key(*provider))
            .map(take_provider)
            .or_else(|| {
                ready_providers
                    .iter()
                    .find_map(|provider| by_provider.get(provider).cloned())
            })
            .or_else(|| {
                preferred
                    .iter()
                    .find(|provider| by_provider.contains_key(*provider))
                    .map(take_provider)
            })
            .or_else(|| by_provider.values().next().cloned())
            .unwrap_or_default(),
        ProviderPreference::Require(required) => {
            if required.is_empty() {
                return Err(MarketError::InvalidSubscription(
                    "Require provider preference must not be empty".into(),
                ));
            }
            let missing = required
                .iter()
                .filter(|provider| !by_provider.contains_key(*provider))
                .map(ToString::to_string)
                .collect::<Vec<_>>();
            if !missing.is_empty() {
                return Err(MarketError::NotFound(format!(
                    "required Market data provider is unavailable: {}",
                    missing.join(", ")
                )));
            }
            required.iter().flat_map(take_provider).collect()
        },
        ProviderPreference::AllEligible => by_provider.values().flatten().cloned().collect(),
    };
    if selected.is_empty() {
        return Err(MarketError::NotFound(
            "the selected Market has no eligible data provider".into(),
        ));
    }
    Ok(selected)
}

fn required_providers(preference: &ProviderPreference) -> Vec<kairos_primitives::market::Provider> {
    match preference {
        ProviderPreference::Require(providers) => providers.clone(),
        _ => Vec::new(),
    }
}

fn snapshot_timer_name(source_id: &MarketFeedId) -> String {
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
                || contains(connections.massive_futures_websocket.keys())
                || contains(connections.massive_indices_websocket.keys())
                || contains(connections.massive_forex_websocket.keys())
                || contains(connections.massive_crypto_websocket.keys())
                || contains(connections.ibkr_market_data.keys())
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
        source_id: &MarketFeedId,
    ) -> Result<BTreeMap<PhysicalSubscriptionKey, crate::ResolvedMarket>, MarketError> {
        Ok(self
            .desired_source_subscriptions()
            .map_err(MarketError::Invalid)?
            .remove(source_id)
            .unwrap_or_default())
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
            let wanted = self.desired_managed_markets(&source_id)?;
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
        let Ok(source_id) = MarketFeedId::new(value) else {
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
        let source_id = MarketFeedId::new(connection_key)
            .map_err(|error| MarketError::Invalid(error.to_string()))?;
        if !self.actor.attached_sources.contains_key(&source_id) {
            return Ok(());
        }
        let mut markets = self
            .desired_managed_markets(&source_id)?
            .into_values()
            .filter(|market| {
                market.runtime_route().is_some_and(|binding| {
                    binding
                        .subscription_symbol
                        .eq_ignore_ascii_case(event.symbol.as_str())
                })
            })
            .filter_map(|market| market.data_route().map(|route| (route, market)))
            .collect::<BTreeMap<_, _>>()
            .into_values();
        let Some(market) = markets.next() else {
            return Ok(());
        };
        if let Some(input) = normalize(&market, event)
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
            MarketFeedId::new(value).map_err(|error| MarketError::Invalid(error.to_string()))?;
        let markets = self
            .desired_managed_markets(&source_id)?
            .into_values()
            .filter_map(|market| market.data_route().map(|route| (route, market)))
            .collect::<BTreeMap<_, _>>();
        if markets.is_empty() {
            return Ok(());
        }
        let symbols = markets
            .values()
            .map(|market| {
                kairos_primitives::integration::ParticipantSymbol::new(
                    market
                        .runtime_route()
                        .expect("managed Market has a runtime binding")
                        .subscription_symbol
                        .as_str(),
                )
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
    try_family!(massive_futures_websocket);
    try_family!(massive_indices_websocket);
    try_family!(massive_forex_websocket);
    try_family!(massive_crypto_websocket);
    try_family!(ibkr_market_data);
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
    try_family!(massive_futures_websocket);
    try_family!(massive_indices_websocket);
    try_family!(massive_forex_websocket);
    try_family!(massive_crypto_websocket);
    try_family!(ibkr_market_data);
    Err(IntegrationError::Unavailable(format!(
        "managed Market stream connection is missing: {key}"
    )))
}

async fn managed_fetch_quotes(
    context: &mut Context<'_, MarketApplication>,
    key: &ConnectionKey,
    symbols: &[kairos_primitives::integration::ParticipantSymbol],
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

fn rpc_market_error(error: MarketError) -> ErrorObjectOwned {
    let error = MarketControlError {
        code: "market.request_failed".into(),
        message: error.to_string(),
        retryable: matches!(
            error,
            MarketError::SourceUnavailable(_)
                | MarketError::QueueOverflow(_)
                | MarketError::Recovery(_)
        ),
        details: Default::default(),
    };
    business_error(MARKET_BUSINESS_ERROR_CODE, error.message.clone(), error)
}

fn rpc_invalid(error: impl std::fmt::Display) -> ErrorObjectOwned {
    business_error(
        MARKET_BUSINESS_ERROR_CODE,
        error.to_string(),
        MarketControlError {
            code: "market.invalid_control_payload".into(),
            message: error.to_string(),
            retryable: false,
            details: Default::default(),
        },
    )
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use kairos_market_contract::{MarketDataRouteState, MarketDataRoutesQuery};
    use kairos_primitives::decimal::Price;
    use kairos_primitives::reference::{
        AssetClass, ExchangeId, InstrumentId, InstrumentKind, MarketId,
    };
    use kairos_primitives::runtime::InstanceIdentity;
    use kairos_primitives::time::{Generation, Sequence, UnixNanos};

    use super::*;
    use crate::{ObservationKind, ProviderRouteBinding, ReconcileMarketUniverse, ResolvedMarket};

    fn market_with_provider(provider: &str) -> ResolvedMarket {
        ResolvedMarket::new_with_binding(
            "market:exchange:nasdaq:equity:AAPL",
            "instrument:equity:US:AAPL:common",
            InstrumentKind::Equity,
            "exchange:nasdaq",
            ProviderRouteBinding::new(provider, "equity", "AAPL")
                .unwrap()
                .with_observation_capabilities([ObservationKind::Quote]),
        )
        .unwrap()
    }

    #[test]
    fn automatic_and_prefer_select_ready_provider_routes() {
        let mut market = market_with_provider("binance");
        market
            .merge_data_routes(&market_with_provider("massive"))
            .unwrap();
        let massive = kairos_primitives::market::Provider::new("massive").unwrap();
        let ready = std::collections::BTreeSet::from([massive.clone()]);

        let automatic =
            select_provider_routes(vec![market.clone()], &ProviderPreference::Automatic, &ready)
                .unwrap();
        assert_eq!(automatic[0].selected_provider.as_ref(), Some(&massive));

        let binance = kairos_primitives::market::Provider::new("binance").unwrap();
        let preferred = select_provider_routes(
            vec![market],
            &ProviderPreference::Prefer(vec![binance]),
            &ready,
        )
        .unwrap();
        assert_eq!(preferred[0].selected_provider.as_ref(), Some(&massive));
    }

    #[test]
    fn data_routes_discovers_configured_source_plan_for_market_identity() {
        let mut application = MarketApplication::new("market", 10).unwrap();
        let market_id = MarketId::new("market:exchange:nasdaq:equity:AAPL").unwrap();
        let mut market = ResolvedMarket::new_with_binding(
            market_id.as_str(),
            "instrument:equity:US:AAPL:common",
            InstrumentKind::Equity,
            "exchange:nasdaq",
            ProviderRouteBinding::new("massive", "equity", "AAPL")
                .unwrap()
                .with_observation_capabilities([ObservationKind::Quote]),
        )
        .unwrap();
        market.asset_type = Some(AssetClass::Equity);
        application
            .reconcile_market_universe(ReconcileMarketUniverse {
                generation: Generation::new(1),
                event_sequence: Sequence::new(1),
                markets: vec![market],
            })
            .unwrap();
        let source = FeedDescriptor::for_provider(
            MarketFeedId::new("massive-equity").unwrap(),
            "massive",
            ExchangeId::new("massive").unwrap(),
            "equity",
            Some("equity".into()),
        )
        .unwrap()
        .with_observation_capabilities([ObservationKind::Quote]);
        application
            .configure_conflux(
                Duration::from_secs(1),
                Duration::from_secs(1),
                Duration::from_secs(1),
                InstanceIdentity::unscoped("workspace").unwrap(),
                vec![MarketSourcePlan {
                    descriptor: source,
                    mode: MarketSourceMode::Snapshot(Duration::from_secs(1)),
                }],
                None,
                None,
            )
            .unwrap();

        let response = application.data_routes_control(MarketDataRoutesQuery {
            market_id: Some(market_id),
            observation_kind: Some(ObservationKind::Quote),
            provider: Some(kairos_primitives::market::Provider::new("massive").unwrap()),
            configured_only: true,
            ..Default::default()
        });

        assert_eq!(response.routes.len(), 1);
        assert_eq!(
            response.routes[0].market_id,
            "market:exchange:nasdaq:equity:AAPL"
        );
        assert_eq!(response.routes[0].provider.as_str(), "massive");
        assert_eq!(
            response.routes[0].observation_kinds,
            vec![ObservationKind::Quote]
        );
        assert_eq!(response.routes[0].state, MarketDataRouteState::Configured);
        assert!(!response.routes[0].selected);

        let incompatible = application.data_routes_control(MarketDataRoutesQuery {
            observation_kind: Some(ObservationKind::Trade),
            configured_only: true,
            ..Default::default()
        });
        assert!(incompatible.routes.is_empty());

        let ready = application.data_routes_control(MarketDataRoutesQuery {
            observation_kind: Some(ObservationKind::Quote),
            ready_only: true,
            ..Default::default()
        });
        assert!(ready.routes.is_empty());
    }

    #[test]
    fn data_routes_discovers_configured_source_plan_for_options_target() {
        let mut application = MarketApplication::new("market", 10).unwrap();
        let underlying_market_id = MarketId::new("market:exchange:nasdaq:equity:SPY").unwrap();
        let underlying_instrument_id =
            InstrumentId::new("instrument:equity:US:SPY:common").unwrap();
        let mut underlying = ResolvedMarket::new_with_binding(
            underlying_market_id.as_str(),
            underlying_instrument_id.as_str(),
            InstrumentKind::Equity,
            "exchange:nasdaq",
            ProviderRouteBinding::new("massive", "equity", "SPY").unwrap(),
        )
        .unwrap();
        underlying.asset_type = Some(AssetClass::Equity);
        let mut option = ResolvedMarket::new_with_binding(
            "market:opra:option:SPY260101C00450000",
            "instrument:option:US:SPY:20260101:C:450",
            InstrumentKind::Option,
            "exchange:nasdaq",
            ProviderRouteBinding::new("massive", "options", "O:SPY260101C00450000").unwrap(),
        )
        .unwrap();
        option.asset_type = Some(AssetClass::Equity);
        option.underlying_instrument_id = Some(underlying_instrument_id);
        option.expiry_unix_nanos = Some(UnixNanos::new(1_767_225_600_000_000_000));
        option.strike = Some(Price::new(450, 0).unwrap());
        option.option_right = Some("CALL".into());
        application
            .reconcile_market_universe(ReconcileMarketUniverse {
                generation: Generation::new(1),
                event_sequence: Sequence::new(1),
                markets: vec![underlying, option],
            })
            .unwrap();
        let source = FeedDescriptor::for_provider(
            MarketFeedId::new("massive-options").unwrap(),
            "massive",
            ExchangeId::new("massive").unwrap(),
            "options",
            Some("equity".into()),
        )
        .unwrap();
        application
            .configure_conflux(
                Duration::from_secs(1),
                Duration::from_secs(1),
                Duration::from_secs(1),
                InstanceIdentity::unscoped("workspace").unwrap(),
                vec![MarketSourcePlan {
                    descriptor: source,
                    mode: MarketSourceMode::MarketScopedStream,
                }],
                None,
                None,
            )
            .unwrap();

        let response = application.data_routes_control(MarketDataRoutesQuery {
            market_id: Some(MarketId::new("market:opra:option:SPY260101C00450000").unwrap()),
            ..Default::default()
        });

        assert_eq!(response.routes.len(), 1);
        assert_eq!(response.routes[0].provider.as_str(), "massive");
        assert_eq!(response.routes[0].state, MarketDataRouteState::Configured);
    }
}
