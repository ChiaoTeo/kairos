use std::fmt;

use kairos_protocol::InstanceIdentity;
use kairos_workspace::Workspace;

use crate::application::{load_replay_events, MarketProcessSettings};
use crate::composition::history::{spawn_jsonl_history, HistoryCollectionSpec};
use crate::services::source::load_replay_checkpoint;
use crate::{MarketApplication, MarketDataRoute, MarketProcess, ResolvedMarket, SubscriptionId};

use super::super::reference::{project_market_universe, spawn_market_universe_watcher};

use super::super::{
    attach_replay_source_with_policy, ConfiguredMarketSourceActivator, MarketCompositionConfig,
    MarketProcessRequest, MarketRuntimeProfile, MarketRuntimeScope, MmapMarketChangePublisher,
};

const VIEW_SLOT_SIZE: usize = 4_194_304;
const MAX_DYNAMIC_MEMBERS: usize = 10_000;

fn collection_market_descriptor(
    reference: &kairos_reference_contract::ReferenceProjectionSnapshot,
    sources: &std::collections::BTreeMap<String, super::super::config::MarketSourceBinding>,
    name: &str,
    collection: &super::super::config::MarketCollectionConfig,
) -> Result<ResolvedMarket, MarketStartupError> {
    if collection.market_id.is_some() == collection.instrument_id.is_some() {
        return Err(MarketStartupError::new(format!(
            "Market collection {name} requires exactly one of market_id or instrument_id"
        )));
    }
    let source_id = collection.source_id.as_deref().ok_or_else(|| {
        MarketStartupError::new(format!(
            "Market collection {name} requires source_id; provider routes are Market-owned"
        ))
    })?;
    let binding = sources.get(source_id).ok_or_else(|| {
        MarketStartupError::new(format!(
            "Market collection {name} references unknown source {source_id}"
        ))
    })?;
    if !binding.enabled() {
        return Err(MarketStartupError::new(format!(
            "Market collection {name} references disabled source {source_id}"
        )));
    }
    let (provider, provider_product) = super::super::sources::binding_provider_product(binding);
    let target_id = collection
        .market_id
        .as_deref()
        .or(collection.instrument_id.as_deref())
        .expect("collection identity validated");
    let route = MarketDataRoute::new(
        format!("market-route:{source_id}:{target_id}"),
        provider,
        provider_product,
        collection.subject.clone(),
    )
    .map_err(MarketStartupError::new)?
    .with_observation_capabilities(super::super::reference::adapter_observation_capabilities(
        provider,
        provider_product,
    ));
    let mut descriptor = if let Some(market_id) = collection.market_id.as_deref() {
        let market = reference
            .markets
            .iter()
            .find(|market| market.market_id == market_id)
            .ok_or_else(|| {
                MarketStartupError::new(format!(
                    "Market collection {name} references missing market {market_id}"
                ))
            })?;
        let instrument = reference
            .instruments
            .iter()
            .find(|instrument| instrument.instrument_id == market.instrument_id)
            .ok_or_else(|| {
                MarketStartupError::new(format!(
                    "Market collection {name} references missing instrument {}",
                    market.instrument_id
                ))
            })?;
        let mut value = ResolvedMarket::new(
            market.market_id.clone(),
            market.instrument_id.clone(),
            instrument.instrument_type,
            market.exchange_id.clone(),
            route,
        )
        .map_err(MarketStartupError::new)?;
        value.asset_type = market.asset_type;
        value.underlying_instrument_id = market
            .underlying_instrument_id
            .clone()
            .map(kairos_primitives::InstrumentId::new)
            .transpose()
            .map_err(MarketStartupError::new)?;
        value
    } else {
        let instrument_id = collection
            .instrument_id
            .as_deref()
            .expect("collection identity validated");
        let instrument = reference
            .instruments
            .iter()
            .find(|instrument| instrument.instrument_id == instrument_id)
            .ok_or_else(|| {
                MarketStartupError::new(format!(
                    "Market collection {name} references missing instrument {instrument_id}"
                ))
            })?;
        let mut value = ResolvedMarket::consolidated(
            instrument.instrument_id.clone(),
            collection.network_id.clone(),
            instrument.instrument_type,
            route,
        )
        .map_err(MarketStartupError::new)?;
        value.underlying_instrument_id = instrument
            .underlying_instrument_id
            .clone()
            .map(kairos_primitives::InstrumentId::new)
            .transpose()
            .map_err(MarketStartupError::new)?;
        value.asset_type = collection
            .asset_type
            .as_deref()
            .map(str::parse)
            .transpose()
            .map_err(|error| MarketStartupError::new(format!("{error}")))?;
        value
    };
    descriptor = descriptor
        .with_source(source_id)
        .map_err(MarketStartupError::new)?;
    Ok(descriptor)
}

fn reference_endpoint(
    workspace: &Workspace,
) -> Result<kairos_reference_contract::ReferenceEndpoint, MarketStartupError> {
    Ok(kairos_reference_contract::ReferenceEndpoint {
        database: workspace
            .child(&["state", "reference", "reference.sqlite"])
            .map_err(MarketStartupError::new)?,
        actor_id: "reference-actor".into(),
        aeron_dir: std::env::var("AERON_DIR").ok(),
        aeron_channel: kairos_transport::DEFAULT_CHANNEL.into(),
        event_stream_id: kairos_transport::stream_ids::REFERENCE_CHANGES,
    })
}

fn read_reference_snapshot(
    client: &kairos_reference_contract::ReferenceClient,
) -> Result<kairos_reference_contract::ReferenceProjectionSnapshot, MarketStartupError> {
    client.market_snapshot().map_err(MarketStartupError::new)
}

#[derive(Debug)]
pub struct MarketStartupError(String);

impl MarketStartupError {
    fn new(error: impl fmt::Display) -> Self {
        Self(error.to_string())
    }
}

impl fmt::Display for MarketStartupError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for MarketStartupError {}

/// Resolve identity, runtime profile, resources, concrete sources and wire
/// implementations into one complete process. The server binary deliberately
/// has no provider, endpoint, credential or transport construction branches.
pub async fn build_market_process(
    request: MarketProcessRequest,
) -> Result<MarketProcess, MarketStartupError> {
    let workspace = Workspace::open(&request.workspace).map_err(MarketStartupError::new)?;
    let instance = request
        .launch_id
        .as_deref()
        .map(|launch_id| workspace.instance(&request.launch_mode, launch_id, &request.instance_id))
        .transpose()
        .map_err(MarketStartupError::new)?;
    if let Some(instance) = &instance {
        instance.prepare().map_err(MarketStartupError::new)?;
    }
    let profile = MarketRuntimeProfile::resolve(
        &workspace,
        request.runtime_profile.as_deref(),
        instance.is_some(),
    )
    .map_err(MarketStartupError::new)?;
    let process_lock = if let Some(instance) = &instance {
        instance
            .process_lock("market")
            .map_err(MarketStartupError::new)?
    } else {
        workspace
            .process_lock("market")
            .map_err(MarketStartupError::new)?
    };
    let identity = instance
        .as_ref()
        .map(|value| InstanceIdentity::new(workspace.id(), value.launch_id(), value.instance_id()))
        .unwrap_or_default();
    let snapshot_path = instance
        .as_ref()
        .map(|value| value.service_snapshot("market"))
        .transpose()
        .map_err(MarketStartupError::new)?
        .unwrap_or(
            workspace
                .service_snapshot("market")
                .map_err(MarketStartupError::new)?,
        );
    let socket_path = instance
        .as_ref()
        .map(|value| value.socket("market"))
        .transpose()
        .map_err(MarketStartupError::new)?
        .unwrap_or(
            workspace
                .process_socket("market")
                .map_err(MarketStartupError::new)?,
        );
    let event_socket_path = instance
        .as_ref()
        .map(|value| value.socket("market-events"))
        .transpose()
        .map_err(MarketStartupError::new)?
        .unwrap_or(
            workspace
                .process_socket("market-events")
                .map_err(MarketStartupError::new)?,
        );
    let market_config = if profile.scope != MarketRuntimeScope::Replay {
        MarketCompositionConfig::load(&workspace).map_err(MarketStartupError::new)?
    } else {
        MarketCompositionConfig::default()
    };
    let reference_client = (profile.scope != MarketRuntimeScope::Replay)
        .then(|| {
            reference_endpoint(&workspace).map(kairos_reference_contract::ReferenceClient::connect)
        })
        .transpose()?;
    let initial_reference_snapshot = reference_client
        .as_ref()
        .map(read_reference_snapshot)
        .transpose()?;

    if let Some(parent) = snapshot_path.parent() {
        std::fs::create_dir_all(parent).map_err(MarketStartupError::new)?;
    }
    let replay_checkpoint_path = if profile.scope == MarketRuntimeScope::Replay {
        instance
            .as_ref()
            .map(|value| value.checkpoint("market", "replay.json"))
            .transpose()
            .map_err(MarketStartupError::new)?
    } else {
        None
    };
    let restored_checkpoint = replay_checkpoint_path
        .as_deref()
        .map(load_replay_checkpoint)
        .transpose()
        .map_err(MarketStartupError::new)?
        .flatten();
    let actor_id = format!("market:{}", profile.name);
    let mut application = match restored_checkpoint {
        Some(checkpoint) => MarketApplication::restore_with_source_capacity(
            checkpoint,
            MAX_DYNAMIC_MEMBERS,
            profile.source_input_capacity,
        ),
        None => MarketApplication::new_with_source_capacity(
            actor_id,
            MAX_DYNAMIC_MEMBERS,
            profile.source_input_capacity,
        ),
    }
    .map_err(MarketStartupError::new)?;
    if let Some(snapshot) = initial_reference_snapshot.as_ref() {
        application
            .reconcile_market_universe(
                project_market_universe(snapshot, &market_config.sources)
                    .map_err(MarketStartupError::new)?,
            )
            .map_err(MarketStartupError::new)?;
    }
    match profile.scope {
        MarketRuntimeScope::Replay => {
            let instance = instance.as_ref().ok_or_else(|| {
                MarketStartupError::new("Market replay requires instance resources")
            })?;
            let data_path = instance
                .state(&["market", "replay.jsonl"])
                .map_err(MarketStartupError::new)?;
            let checkpoint_path =
                replay_checkpoint_path.expect("replay checkpoint path resolved above");
            let events = load_replay_events(&data_path).map_err(MarketStartupError::new)?;
            let replay = profile.replay.as_ref().ok_or_else(|| {
                MarketStartupError::new("Market replay profile has no replay policy")
            })?;
            attach_replay_source_with_policy(
                &mut application,
                events,
                replay.start_unix_nanos,
                replay.end_unix_nanos,
                checkpoint_path,
                replay.clock,
                replay.speed_multiplier,
                replay.start_paused,
            )
            .map_err(MarketStartupError::new)?;
        }
        MarketRuntimeScope::Shared
        | MarketRuntimeScope::Instance
        | MarketRuntimeScope::Diagnostic => {}
    }

    let mut history_specs = Vec::new();
    if profile.scope != MarketRuntimeScope::Replay {
        for (name, collection) in &market_config.collections {
            if !collection.enabled {
                continue;
            }
            if name.trim().is_empty()
                || name == "."
                || name == ".."
                || name.contains('/')
                || name.contains('\\')
                || collection.subject.trim().is_empty()
            {
                return Err(MarketStartupError::new(format!(
                    "invalid Market collection identity: {name}"
                )));
            }
            if collection.queue_capacity == 0 {
                return Err(MarketStartupError::new(format!(
                    "Market collection {name} queue_capacity must be positive"
                )));
            }
            let reference = initial_reference_snapshot.as_ref().ok_or_else(|| {
                MarketStartupError::new(format!(
                    "Market collection {name} requires the Reference current view"
                ))
            })?;
            let descriptor =
                collection_market_descriptor(reference, &market_config.sources, name, collection)?;
            let subscription_id = SubscriptionId::new(format!("collection:{name}"))
                .map_err(MarketStartupError::new)?;
            let owner_id = format!("collection:{name}");
            application
                .subscribe_static_with_selectors(
                    subscription_id,
                    owner_id,
                    descriptor.clone(),
                    collection
                        .selectors
                        .iter()
                        .map(|value| crate::ObservationSelector::parse(value))
                        .collect::<Result<Vec<_>, _>>()
                        .map_err(MarketStartupError::new)?,
                )
                .map_err(MarketStartupError::new)?;
            history_specs.push(HistoryCollectionSpec {
                name: name.clone(),
                scope_key: descriptor.scope.key(),
                selectors: collection.selectors.clone(),
                root: workspace
                    .data_root()
                    .join("market")
                    .join("collections")
                    .join(name),
                queue_capacity: collection.queue_capacity,
            });
        }
        if !history_specs.is_empty() {
            let mut activator = ConfiguredMarketSourceActivator::new(workspace.clone());
            application
                .activate_sources_for_subscriptions(&mut activator)
                .await
                .map_err(MarketStartupError::new)?;
            application
                .sync_source_subscriptions()
                .await
                .map_err(MarketStartupError::new)?;
        }
    }

    let publisher = MmapMarketChangePublisher::create_with_identity(
        &snapshot_path,
        VIEW_SLOT_SIZE,
        format!("market:{}", profile.name),
        identity.clone(),
    )
    .map_err(MarketStartupError::new)?;
    let settings = MarketProcessSettings {
        publication_interval: profile.snapshot_interval,
        freshness_check_interval: profile.freshness_check_interval,
        freshness_max_age: profile.freshness_max_age,
        reference_recovery_interval: profile.reference_recovery_interval,
        shutdown_timeout: profile.shutdown_timeout,
        publication_queue_capacity: profile.publication_queue_capacity,
    };
    let mut process = MarketProcess::new_configured_with_activator(
        application,
        publisher,
        socket_path,
        event_socket_path,
        identity,
        settings,
        (profile.scope != MarketRuntimeScope::Replay)
            .then(|| Box::new(ConfiguredMarketSourceActivator::new(workspace.clone())) as Box<_>),
        crate::composition::publication::encode_event,
    )
    .map_err(MarketStartupError::new)?;
    if !history_specs.is_empty() {
        process = process.with_history_recorder(
            spawn_jsonl_history(history_specs).map_err(MarketStartupError::new)?,
        );
    }
    // A static replay resolves subscriptions from its explicit request and
    // must remain independent of the live Reference/Aeron runtime.
    let watcher_guard = if profile.scope != MarketRuntimeScope::Replay {
        let client =
            kairos_reference_contract::ReferenceClient::connect(reference_endpoint(&workspace)?);
        let (updates, guard) = spawn_market_universe_watcher(
            client,
            market_config.sources.clone(),
            profile.reference_recovery_interval,
            profile.publication_queue_capacity.max(1),
        )
        .map_err(MarketStartupError::new)?;
        process = process.with_market_universe_updates(updates);
        Some(guard)
    } else {
        None
    };
    if profile.scope != MarketRuntimeScope::Replay {
        process = process.without_event_socket().with_aeron_event_publisher(
            kairos_transport::AeronBytePublisher::connect(
                std::env::var("AERON_DIR").ok().as_deref(),
                kairos_transport::DEFAULT_CHANNEL,
                kairos_transport::stream_ids::MARKET_EVENTS,
            )
            .map_err(MarketStartupError::new)?,
        );
    }
    Ok(process.with_lifecycle_guard((process_lock, watcher_guard)))
}
