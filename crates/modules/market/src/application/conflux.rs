use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::Duration;

use kairos_conflux::{
    ConfluxActor, ConfluxEvent, ConnectionKey, Context, ExternalParticipantEvent, IntegrationError,
    MarketQuoteQuery, MarketSubscriptionCommand, MmapOutputDeclaration, SnapshotEnvelopeMetadata,
    SystemEvent,
};
use kairos_market_contract::{
    MarketCommandOutcome, MarketCommandStatus, MarketControlError, MarketDataSource,
    MarketDataSourcesResponse, MarketFeedStatus, MarketHealthResponse, MarketHealthStatus,
    MarketOperation, MarketReleaseOwnerPayload, MarketReleaseOwnerResponse, MarketSourceStatus,
    MarketSubscribePayload, MarketSubscriptionResponse, MarketSubscriptionStatus,
    MarketUnsubscribePayload, MarketViewPublisher, SubscriptionOwnerKey,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::control::jsonrpc::{ErrorObjectOwned, RpcResult, business_error};

use super::{
    MarketApplication, MarketError, MarketRpcActor, OptionSelectionFilter, resolve_market,
    resolve_market_by_id, resolve_option_markets,
};
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

const MARKET_BUSINESS_ERROR_CODE: i32 = -31_006;

pub struct MarketLocalEvent(SourceInput);

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

pub(crate) struct MarketConfluxState {
    freshness_interval: Duration,
    freshness_max_age: Duration,
    shutdown_timeout: Duration,
    identity: InstanceIdentity,
    producer_incarnation: u64,
    source_plans: BTreeMap<String, MarketSourcePlan>,
    history: Option<HistoryQueue>,
    reference_projection: Option<ReferenceProjectionState>,
    command_results: BTreeMap<String, (serde_json::Value, serde_json::Value)>,
    view_publication: Option<MarketViewPublication>,
}

struct MarketViewPublication {
    root: PathBuf,
    slot_size: usize,
    revision: u64,
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
            reference_projection: None,
            command_results: BTreeMap::new(),
            view_publication: None,
        }
    }
}

impl MarketApplication {
    pub(crate) fn configure_view_publication(
        &mut self,
        root: PathBuf,
        slot_size: usize,
    ) -> Result<(), String> {
        if slot_size == 0 {
            return Err("Market mmap view slot size must be positive".into());
        }
        self.conflux.view_publication = Some(MarketViewPublication {
            root,
            slot_size,
            revision: 1,
        });
        Ok(())
    }

    pub(crate) fn configure_conflux(
        &mut self,
        freshness_interval: Duration,
        freshness_max_age: Duration,
        shutdown_timeout: Duration,
        identity: InstanceIdentity,
        source_plans: Vec<MarketSourcePlan>,
        history: Option<HistoryQueue>,
        reference_projection: Option<ReferenceProjectionConfig>,
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
        self.conflux.reference_projection =
            reference_projection.map(|config| ReferenceProjectionState {
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
    type LocalEvent = MarketLocalEvent;

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

    async fn data_sources(
        &mut self,
        query: kairos_market_contract::MarketDataSourcesQuery,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketDataSourcesResponse> {
        let response = self.data_sources_control(query);
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
    fn data_sources_control(
        &self,
        query: kairos_market_contract::MarketDataSourcesQuery,
    ) -> MarketDataSourcesResponse {
        let view = self.current_view();
        let has_filter = query.market_id.is_some()
            || query.instrument_id.is_some()
            || query.underlying_market_id.is_some()
            || query.underlying_instrument_id.is_some()
            || query.exchange.is_some()
            || query.market_type.is_some()
            || query.asset_type.is_some()
            || query.target.is_some()
            || query.observation_kind.is_some()
            || query.provider_id.is_some();
        let market_universe = self.market_universe();
        let option_target = query
            .target
            .as_deref()
            .is_some_and(|value| value.eq_ignore_ascii_case("options"));
        let underlying_instrument_id = query
            .underlying_instrument_id
            .as_ref()
            .map(|value| value.as_str())
            .or_else(|| {
                query.underlying_market_id.as_ref().and_then(|market_id| {
                    market_universe
                        .iter()
                        .find(|market| market.market_id() == Some(market_id))
                        .map(|market| market.instrument_id.as_str())
                })
            });
        let matching_markets = market_universe
            .iter()
            .filter(|market| {
                (!option_target
                    || market.instrument_kind
                        == kairos_primitives::reference::InstrumentKind::Option)
                    && underlying_instrument_id.is_none_or(|value| {
                        market.underlying_instrument_id.as_deref() == Some(value)
                    })
                    && query
                        .market_id
                        .as_ref()
                        .is_none_or(|value| market.market_id() == Some(value))
                    && query
                        .instrument_id
                        .as_ref()
                        .is_none_or(|value| &market.instrument_id == value)
                    && query
                        .exchange
                        .as_ref()
                        .is_none_or(|value| market.exchange_id.as_ref() == Some(value))
                    && query
                        .market_type
                        .as_ref()
                        .is_none_or(|value| market.instrument_kind.as_str() == value.as_str())
                    && query.asset_type.as_ref().is_none_or(|value| {
                        market.asset_type.map(|class| class.as_str()) == Some(value.as_str())
                    })
                    && query
                        .provider_id
                        .as_ref()
                        .is_none_or(|value| market.route.provider_id.as_str() == value.as_str())
                    && query
                        .observation_kind
                        .is_none_or(|kind| market.route.observation_capabilities.contains(&kind))
            })
            .collect::<Vec<_>>();
        let accepts_query = |descriptor: &SourceDescriptor| {
            query
                .observation_kind
                .is_none_or(|kind| descriptor.observation_capabilities.contains(&kind))
                && (!has_filter
                    || matching_markets
                        .iter()
                        .any(|market| super::source_accepts(descriptor, market)))
        };
        let provider_for = |descriptor: &SourceDescriptor| {
            matching_markets
                .iter()
                .find(|market| super::source_accepts(descriptor, market))
                .map(|market| market.route.provider_id.clone())
        };
        let mut sources = BTreeMap::<SourceId, MarketDataSource>::new();
        if !query.ready_only {
            for plan in self.conflux.source_plans.values() {
                if accepts_query(&plan.descriptor) {
                    sources.insert(
                        plan.descriptor.id.clone(),
                        source_control_state(
                            &plan.descriptor,
                            SourceStatus::Stopped,
                            provider_for(&plan.descriptor),
                        ),
                    );
                }
            }
        }
        for source in view.sources.values() {
            if accepts_query(&source.descriptor)
                && (!query.ready_only || source.status == SourceStatus::Ready)
            {
                sources.insert(
                    source.descriptor.id.clone(),
                    source_control_state(
                        &source.descriptor,
                        source.status,
                        provider_for(&source.descriptor),
                    ),
                );
            }
        }
        if !query.configured_only && !query.ready_only {
            for market in &matching_markets {
                let has_configured_source = self
                    .conflux
                    .source_plans
                    .values()
                    .any(|plan| super::source_accepts(&plan.descriptor, market));
                if has_configured_source {
                    continue;
                }
                let source_id = market.source_id.clone().unwrap_or_else(|| {
                    SourceId::new(market.route.provider_id.as_str())
                        .expect("provider identity is a valid fallback source identity")
                });
                sources
                    .entry(source_id.clone())
                    .or_insert(MarketDataSource {
                        source_id,
                        provider_id: Some(market.route.provider_id.clone()),
                        observation_capabilities: market
                            .route
                            .observation_capabilities
                            .iter()
                            .copied()
                            .collect(),
                        configured: false,
                        status: MarketSourceStatus::Disconnected,
                        ready: false,
                        stale: false,
                    });
            }
        }
        MarketDataSourcesResponse {
            sources: sources.into_values().collect(),
        }
    }

    fn cached_control_response<T>(
        &self,
        key: &str,
        request: &serde_json::Value,
    ) -> Option<RpcResult<T>>
    where
        T: serde::de::DeserializeOwned,
    {
        self.conflux
            .command_results
            .get(key)
            .map(|(cached_request, cached_response)| {
                if cached_request == request {
                    serde_json::from_value(cached_response.clone()).map_err(rpc_invalid)
                } else {
                    Err(idempotency_conflict())
                }
            })
    }

    fn remember_control_response<T>(
        &mut self,
        key: String,
        request: serde_json::Value,
        response: &T,
    ) -> RpcResult<()>
    where
        T: serde::Serialize,
    {
        const MAX_COMMAND_RESULTS: usize = 4_096;
        let response = serde_json::to_value(response).map_err(rpc_invalid)?;
        self.conflux
            .command_results
            .insert(key, (request, response));
        while self.conflux.command_results.len() > MAX_COMMAND_RESULTS {
            let Some(oldest) = self.conflux.command_results.keys().next().cloned() else {
                break;
            };
            self.conflux.command_results.remove(&oldest);
        }
        Ok(())
    }

    async fn subscribe_control(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<MarketSubscribePayload>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketSubscriptionResponse> {
        let key = command.idempotency_key.to_string();
        let request = serde_json::to_value(&command).map_err(rpc_invalid)?;
        if let Some(response) = self.cached_control_response(&key, &request) {
            return response;
        }
        let response = self.subscribe_contract(command).map_err(rpc_market_error)?;
        self.activate_managed_sources(context)
            .map_err(rpc_market_error)?;
        self.sync_all_source_subscriptions(context)
            .await
            .map_err(rpc_market_error)?;
        self.spawn_source_inputs(context);
        self.remember_control_response(key, request, &response)?;
        Ok(response)
    }

    async fn unsubscribe_control(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<MarketUnsubscribePayload>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketCommandStatus> {
        let key = command.idempotency_key.to_string();
        let request = serde_json::to_value(&command).map_err(rpc_invalid)?;
        if let Some(response) = self.cached_control_response(&key, &request) {
            return response;
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
        self.remember_control_response(key, request, &response)?;
        Ok(response)
    }

    async fn release_owner_control(
        &mut self,
        command: kairos_market_contract::MarketCommandEnvelope<MarketReleaseOwnerPayload>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<MarketReleaseOwnerResponse> {
        let key = command.idempotency_key.to_string();
        let request = serde_json::to_value(&command).map_err(rpc_invalid)?;
        if let Some(response) = self.cached_control_response(&key, &request) {
            return response;
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
        self.remember_control_response(key, request, &response)?;
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
        let subscription_id = SubscriptionId::new(command.command_id.as_str())
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
        let explicit_market_id = command
            .payload
            .params
            .get("market_id")
            .and_then(serde_json::Value::as_str)
            .or_else(|| subject.starts_with("market:").then_some(subject))
            .map(kairos_primitives::reference::MarketId::new)
            .transpose()
            .map_err(|error| MarketError::Invalid(error.to_string()))?;
        let options_target = command
            .payload
            .params
            .get("target")
            .and_then(serde_json::Value::as_str)
            .is_some_and(|value| value.eq_ignore_ascii_case("options"));
        let chain = options_target
            || command
                .payload
                .params
                .get("mode")
                .and_then(serde_json::Value::as_str)
                .is_some_and(|value| value.eq_ignore_ascii_case("chain"));
        if command.payload.dynamic && !chain {
            return Err(MarketError::Unsupported(
                "dynamic subscriptions require params.target=options".into(),
            ));
        }
        if command.payload.source_id.is_some() && !command.payload.source_ids.is_empty() {
            return Err(MarketError::InvalidSubscription(
                "use either source_id or source_ids, not both".into(),
            ));
        }
        let source_ids = if command.payload.source_ids.is_empty() {
            command
                .payload
                .source_id
                .iter()
                .cloned()
                .collect::<Vec<_>>()
        } else {
            command.payload.source_ids.clone()
        };
        if chain {
            let filter = option_selection_filter(&command.payload.params, source_ids.clone())?;
            let markets =
                resolve_option_markets(&self.market_universe(), exchange, asset_type, &filter)
                    .map_err(MarketError::InvalidSubscription)?;
            if markets.is_empty() {
                return Err(MarketError::NotFound("no option markets selected".into()));
            }
            let query = crate::MarketSelectionQuery {
                exchange_id: Some(
                    kairos_primitives::reference::Exchange::new(exchange)
                        .map_err(|error| MarketError::Invalid(error.to_string()))?,
                ),
                provider_product: Some(
                    kairos_primitives::integration::ProviderProductCode::new(market_type)
                        .map_err(|error| MarketError::Invalid(error.to_string()))?,
                ),
                source_ids,
                underlying_instrument_id: filter.underlying_instrument_id.clone().or_else(|| {
                    markets
                        .first()
                        .and_then(|market| market.underlying_instrument_id.clone())
                }),
                expiry_from_unix_nanos: filter.expiry_from_unix_nanos,
                expiry_to_unix_nanos: filter.expiry_to_unix_nanos,
                strike_lower: filter.strike_lower,
                strike_upper: filter.strike_upper,
                option_right: filter.option_right.clone(),
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
            let universe = self.market_universe();
            let markets = if source_ids.is_empty() {
                vec![
                    if let Some(market_id) = explicit_market_id.as_ref() {
                        resolve_market_by_id(&universe, market_id, None)
                    } else {
                        resolve_market(&universe, exchange, market_type, asset_type, subject)
                    }
                    .map_err(MarketError::InvalidSubscription)?,
                ]
            } else {
                let market = if explicit_market_id.is_none() {
                    Some(
                        resolve_market(&universe, exchange, market_type, asset_type, subject)
                            .map_err(MarketError::InvalidSubscription)?,
                    )
                } else {
                    None
                };
                source_ids
                    .iter()
                    .map(|source| {
                        if let Some(market_id) = explicit_market_id.as_ref() {
                            resolve_market_by_id(&universe, market_id, Some(source))
                                .map_err(MarketError::InvalidSubscription)
                        } else {
                            market
                                .as_ref()
                                .expect("legacy market was resolved")
                                .clone()
                                .with_source(source.as_str())
                                .map_err(MarketError::InvalidSubscription)
                        }
                    })
                    .collect::<Result<Vec<_>, _>>()?
            };
            self.subscribe_static_many_with_selectors(
                subscription_id.clone(),
                owner.clone(),
                markets,
                selectors,
            )?;
        }
        Ok(MarketSubscriptionResponse {
            subscription_id: subscription_id.clone(),
            owner_id: SubscriptionOwnerKey::new(owner).map_err(MarketError::InvalidSubscription)?,
            status: match self.subscription_status(&subscription_id) {
                Some(crate::SubscriptionStatus::Pending) => MarketSubscriptionStatus::Pending,
                Some(crate::SubscriptionStatus::Ready) => MarketSubscriptionStatus::Ready,
                Some(crate::SubscriptionStatus::Degraded) => MarketSubscriptionStatus::Degraded,
                Some(crate::SubscriptionStatus::Unavailable) => {
                    MarketSubscriptionStatus::Unavailable
                },
                Some(crate::SubscriptionStatus::Rejected) => MarketSubscriptionStatus::Rejected,
                None => MarketSubscriptionStatus::Pending,
            },
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
            let bytes = encode_event(&actor_id, &self.conflux.identity, *sequence, event)
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
            let Some(encoded) = encode_change_view(&actor_id, &self.conflux.identity, change)
                .map_err(MarketError::Recovery)?
            else {
                continue;
            };
            let publication = self.conflux.view_publication.as_ref().ok_or_else(|| {
                MarketError::Recovery(
                    "Market mmap view publication is not configured by Composition".into(),
                )
            })?;
            let resource_key = encoded.key.canonical_key();
            let key = encoded.key;
            let path = MarketViewPublisher::resolved_path(&publication.root, &key)
                .map_err(|error| MarketError::Recovery(error.to_string()))?;
            context
                .outputs()
                .mmap
                .declare(
                    resource_key.clone(),
                    MmapOutputDeclaration {
                        path,
                        slot_capacity: publication.slot_size,
                        revision: publication.revision,
                    },
                )
                .map_err(|error| MarketError::Recovery(error.to_string()))?;
            context
                .outputs()
                .mmap
                .publish(
                    &resource_key,
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

fn source_control_state(
    descriptor: &SourceDescriptor,
    status: SourceStatus,
    provider_id: Option<kairos_primitives::integration::ProviderId>,
) -> MarketDataSource {
    MarketDataSource {
        source_id: descriptor.id.clone(),
        provider_id,
        observation_capabilities: descriptor
            .observation_capabilities
            .iter()
            .copied()
            .collect(),
        configured: true,
        status: match status {
            SourceStatus::Starting => MarketSourceStatus::Connecting,
            SourceStatus::Ready => MarketSourceStatus::Ready,
            SourceStatus::Paused => MarketSourceStatus::Paused,
            SourceStatus::Reconnecting => MarketSourceStatus::Reconnecting,
            SourceStatus::WarmingUp => MarketSourceStatus::WarmingUp,
            SourceStatus::Degraded => MarketSourceStatus::Degraded,
            SourceStatus::Stopped => MarketSourceStatus::Disconnected,
        },
        ready: status == SourceStatus::Ready,
        stale: status == SourceStatus::Degraded,
    }
}

fn option_selection_filter(
    params: &BTreeMap<String, serde_json::Value>,
    source_ids: Vec<SourceId>,
) -> Result<OptionSelectionFilter, MarketError> {
    let filter = params.get("filter").and_then(serde_json::Value::as_object);
    if text_param(filter, "strike_mode")
        .is_some_and(|value| value.eq_ignore_ascii_case("around_spot"))
    {
        return Err(MarketError::Unsupported(
            "spot-relative option strike filters require a current underlying price; pass an absolute strike range"
                .into(),
        ));
    }
    let underlying_symbol = text_param_object(params, "underlying");
    let underlying_market_id = text_param_object(params, "underlying_market_id")
        .map(kairos_primitives::reference::MarketId::new)
        .transpose()
        .map_err(|error| MarketError::Invalid(error.to_string()))?;
    let underlying_instrument_id = text_param_object(params, "underlying_instrument_id")
        .map(kairos_primitives::reference::InstrumentId::new)
        .transpose()
        .map_err(|error| MarketError::Invalid(error.to_string()))?;
    let option_right =
        text_param(filter, "right").filter(|value| !value.eq_ignore_ascii_case("both"));
    Ok(OptionSelectionFilter {
        underlying_market_id,
        underlying_instrument_id,
        underlying_symbol,
        expiry_from_unix_nanos: u64_param(filter, "expiry_from_unix_nanos")
            .map(kairos_primitives::time::UnixNanos::new),
        expiry_to_unix_nanos: u64_param(filter, "expiry_to_unix_nanos")
            .map(kairos_primitives::time::UnixNanos::new),
        strike_lower: price_param(filter, "strike_lower")?,
        strike_upper: price_param(filter, "strike_upper")?,
        option_right,
        limit: u64_param(filter, "limit").map(|value| value as usize),
        source_ids,
    })
}

fn text_param(
    object: Option<&serde_json::Map<String, serde_json::Value>>,
    key: &str,
) -> Option<String> {
    object?
        .get(key)
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn text_param_object(params: &BTreeMap<String, serde_json::Value>, key: &str) -> Option<String> {
    params
        .get(key)
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn u64_param(
    object: Option<&serde_json::Map<String, serde_json::Value>>,
    key: &str,
) -> Option<u64> {
    object?.get(key).and_then(|value| {
        value
            .as_u64()
            .or_else(|| value.as_str().and_then(|text| text.parse::<u64>().ok()))
    })
}

fn price_param(
    object: Option<&serde_json::Map<String, serde_json::Value>>,
    key: &str,
) -> Result<Option<kairos_primitives::decimal::Price>, MarketError> {
    object
        .and_then(|value| value.get(key))
        .map(|value| {
            value
                .as_str()
                .map(str::to_string)
                .unwrap_or_else(|| value.to_string())
                .parse::<kairos_primitives::decimal::Price>()
                .map_err(|error| MarketError::Invalid(error.to_string()))
        })
        .transpose()
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
                    .subscription_symbol
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
                kairos_primitives::integration::ParticipantSymbol::new(
                    market.route.subscription_symbol.as_str(),
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

    use kairos_market_contract::{MarketDataSourcesQuery, MarketSourceStatus};
    use kairos_primitives::decimal::Price;
    use kairos_primitives::reference::{
        AssetClass, Exchange, InstrumentId, InstrumentKind, MarketId,
    };
    use kairos_primitives::runtime::InstanceIdentity;
    use kairos_primitives::time::{Generation, Sequence, UnixNanos};

    use super::*;
    use crate::{MarketDataRoute, ObservationKind, ReconcileMarketUniverse, ResolvedMarket};

    #[test]
    fn data_sources_discovers_configured_source_plan_for_market_identity() {
        let mut application = MarketApplication::new("market", 10).unwrap();
        let market_id = MarketId::new("market:exchange:nasdaq:equity:AAPL").unwrap();
        let mut market = ResolvedMarket::new(
            market_id.as_str(),
            "instrument:equity:US:AAPL:common",
            InstrumentKind::Equity,
            "exchange:nasdaq",
            MarketDataRoute::new("route:aapl", "massive", "equity", "AAPL")
                .unwrap()
                .with_observation_capabilities([ObservationKind::Quote]),
        )
        .unwrap()
        .with_source("massive-equity")
        .unwrap();
        market.asset_type = Some(AssetClass::Equity);
        application
            .reconcile_market_universe(ReconcileMarketUniverse {
                generation: Generation::new(1),
                event_sequence: Sequence::new(1),
                markets: vec![market],
            })
            .unwrap();
        let source = SourceDescriptor::new(
            SourceId::new("massive-equity").unwrap(),
            Exchange::new("data_provider:massive").unwrap(),
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

        let response = application.data_sources_control(MarketDataSourcesQuery {
            market_id: Some(market_id),
            observation_kind: Some(ObservationKind::Quote),
            provider_id: Some(kairos_primitives::integration::ProviderId::new("massive").unwrap()),
            configured_only: true,
            ..Default::default()
        });

        assert_eq!(response.sources.len(), 1);
        assert_eq!(response.sources[0].source_id.as_str(), "massive-equity");
        assert_eq!(
            response.sources[0]
                .provider_id
                .as_ref()
                .map(|value| value.as_str()),
            Some("massive")
        );
        assert_eq!(
            response.sources[0].observation_capabilities,
            vec![ObservationKind::Quote]
        );
        assert!(response.sources[0].configured);
        assert_eq!(response.sources[0].status, MarketSourceStatus::Disconnected);
        assert!(!response.sources[0].ready);

        let incompatible = application.data_sources_control(MarketDataSourcesQuery {
            observation_kind: Some(ObservationKind::Trade),
            configured_only: true,
            ..Default::default()
        });
        assert!(incompatible.sources.is_empty());

        let ready = application.data_sources_control(MarketDataSourcesQuery {
            observation_kind: Some(ObservationKind::Quote),
            ready_only: true,
            ..Default::default()
        });
        assert!(ready.sources.is_empty());
    }

    #[test]
    fn data_sources_discovers_configured_source_plan_for_options_target() {
        let mut application = MarketApplication::new("market", 10).unwrap();
        let underlying_market_id = MarketId::new("market:exchange:nasdaq:equity:SPY").unwrap();
        let underlying_instrument_id =
            InstrumentId::new("instrument:equity:US:SPY:common").unwrap();
        let mut underlying = ResolvedMarket::new(
            underlying_market_id.as_str(),
            underlying_instrument_id.as_str(),
            InstrumentKind::Equity,
            "exchange:nasdaq",
            MarketDataRoute::new("route:spy", "massive", "equity", "SPY").unwrap(),
        )
        .unwrap()
        .with_source("massive-equity")
        .unwrap();
        underlying.asset_type = Some(AssetClass::Equity);
        let mut option = ResolvedMarket::new(
            "market:opra:option:SPY260101C00450000",
            "instrument:option:US:SPY:20260101:C:450",
            InstrumentKind::Option,
            "exchange:nasdaq",
            MarketDataRoute::new(
                "route:spy-option",
                "massive",
                "options",
                "O:SPY260101C00450000",
            )
            .unwrap(),
        )
        .unwrap()
        .with_source("massive-options")
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
        let source = SourceDescriptor::new(
            SourceId::new("massive-options").unwrap(),
            Exchange::new("data_provider:massive").unwrap(),
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

        let response = application.data_sources_control(MarketDataSourcesQuery {
            target: Some("options".into()),
            underlying_market_id: Some(underlying_market_id),
            ..Default::default()
        });

        assert_eq!(response.sources.len(), 1);
        assert_eq!(response.sources[0].source_id.as_str(), "massive-options");
        assert_eq!(response.sources[0].status, MarketSourceStatus::Disconnected);
        assert!(!response.sources[0].ready);
    }
}
