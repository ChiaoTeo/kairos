use std::fmt;

use kairos_protocol::InstanceIdentity;
use kairos_workspace::Workspace;

use crate::application::{load_replay_events, MarketProcessSettings};
use crate::composition::history::{spawn_jsonl_history, HistoryCollectionSpec};
use crate::services::source::load_replay_checkpoint;
use crate::{MarketApplication, MarketDataRoute, MarketProcess, ResolvedMarket, SubscriptionId};

use super::reference::{project_market_universe, spawn_market_universe_watcher};

use super::{
    attach_replay_source_with_policy, ConfiguredMarketSourceActivator, MarketCompositionConfig,
    MarketProcessRequest, MarketRuntimeProfile, MarketRuntimeScope, MmapMarketChangePublisher,
};

const VIEW_SLOT_SIZE: usize = 4_194_304;
const MAX_DYNAMIC_MEMBERS: usize = 10_000;

fn collection_market_descriptor(
    reference: &kairos_reference_contract::ReferenceProjectionSnapshot,
    name: &str,
    collection: &super::config::MarketCollectionConfig,
) -> Result<ResolvedMarket, MarketStartupError> {
    let market_id = collection.market_id.as_deref().ok_or_else(|| {
        MarketStartupError::new(format!(
            "Market collection {name} requires canonical market_id"
        ))
    })?;
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
    let selected = reference
        .market_data_accesses
        .iter()
        .filter(|access| {
            access.market_id == market_id
                && matches!(access.status.as_str(), "active" | "trading")
                && collection
                    .market_data_access_id
                    .as_deref()
                    .is_none_or(|id| id == access.access_id)
        })
        .collect::<Vec<_>>();
    let access = match selected.as_slice() {
        [access] => *access,
        [] => {
            return Err(MarketStartupError::new(format!(
                "Market collection {name} has no selected market-data access"
            )))
        }
        _ => {
            return Err(MarketStartupError::new(format!(
                "Market collection {name} has ambiguous market-data accesses"
            )))
        }
    };
    let route = MarketDataRoute::new(
        access.access_id.clone(),
        access.provider_id.clone(),
        access.provider_product.clone(),
        access.provider_symbol.clone(),
    )
    .map_err(MarketStartupError::new)?;
    let mut descriptor = ResolvedMarket::new(
        market.market_id.clone(),
        market.instrument_id.clone(),
        instrument.instrument_type,
        market.exchange_id.clone(),
        route,
    )
    .map_err(MarketStartupError::new)?;
    descriptor.asset_type = market.asset_type;
    descriptor.underlying_instrument_id = market
        .underlying_instrument_id
        .clone()
        .map(kairos_primitives::InstrumentId::new)
        .transpose()
        .map_err(MarketStartupError::new)?;
    if let Some(source_id) = &collection.source_id {
        descriptor = descriptor
            .with_source(source_id.clone())
            .map_err(MarketStartupError::new)?;
    }
    Ok(descriptor)
}

fn reference_endpoint(
    workspace: &Workspace,
) -> Result<kairos_reference_contract::ReferenceEndpoint, MarketStartupError> {
    Ok(kairos_reference_contract::ReferenceEndpoint {
        database: workspace
            .child(&["reference", "reference.sqlite"])
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
    let reference_client = (profile.scope != MarketRuntimeScope::Replay)
        .then(|| {
            reference_endpoint(&workspace).map(kairos_reference_contract::ReferenceClient::connect)
        })
        .transpose()?;
    let initial_reference_snapshot = reference_client
        .as_ref()
        .and_then(|client| read_reference_snapshot(client).ok());

    if let Some(parent) = snapshot_path.parent() {
        std::fs::create_dir_all(parent).map_err(MarketStartupError::new)?;
    }
    let replay_checkpoint_path = (profile.scope == MarketRuntimeScope::Replay)
        .then(|| {
            instance
                .as_ref()
                .map(|value| value.root().join("checkpoints/market-replay.json"))
        })
        .flatten();
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
                project_market_universe(snapshot).map_err(MarketStartupError::new)?,
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
        let market_config =
            MarketCompositionConfig::load(&workspace).map_err(MarketStartupError::new)?;
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
            let descriptor = collection_market_descriptor(reference, name, collection)?;
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
                market_id: descriptor.market_id.to_string(),
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
