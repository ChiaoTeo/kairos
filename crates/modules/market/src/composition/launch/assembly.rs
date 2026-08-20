use std::fmt;

use kairos_conflux::AeronOutputDeclaration;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_workspace::Workspace;

use super::super::reference::{build_reference_projection, project_market_universe};
use super::super::{
    MarketCompositionConfig, MarketHost, MarketHostRequest, MarketRuntimeProfile,
    MarketRuntimeScope, attach_replay_source_with_policy,
};
use crate::application::load_replay_events;
use crate::composition::history::{HistoryCollectionSpec, spawn_jsonl_history};
use crate::services::source::load_replay_checkpoint;
use crate::{MarketApplication, MarketDataRoute, ResolvedMarket, SubscriptionId};

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
        let mut value = ResolvedMarket::from_reference(
            market.market_id.clone(),
            market.instrument_id.clone(),
            instrument.instrument_type,
            market.exchange_id.clone(),
            route,
        )
        .map_err(MarketStartupError::new)?;
        value.asset_type = market.asset_type;
        value.underlying_instrument_id = market.underlying_instrument_id.clone();
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
        let mut value = ResolvedMarket::consolidated_reference(
            instrument.instrument_id.clone(),
            collection.network_id.clone(),
            instrument.instrument_type,
            route,
        )
        .map_err(MarketStartupError::new)?;
        value.underlying_instrument_id = instrument.underlying_instrument_id.clone();
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
    aeron_dir: Option<&std::path::Path>,
) -> Result<kairos_reference_contract::ReferenceEndpoint, MarketStartupError> {
    Ok(kairos_reference_contract::ReferenceEndpoint {
        database: workspace
            .child(&["state", "reference", "reference.sqlite"])
            .map_err(MarketStartupError::new)?,
        actor_id: kairos_primitives::runtime::ActorId::new("reference-actor")
            .map_err(MarketStartupError::new)?,
        events: kairos_conflux::AeronEndpoint::new(
            aeron_dir.map(std::path::Path::to_path_buf),
            kairos_conflux::DEFAULT_AERON_CHANNEL,
            kairos_conflux::output_stream_ids::REFERENCE_CHANGES,
        )
        .map_err(MarketStartupError::new)?,
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
pub async fn build_market_host(
    request: MarketHostRequest,
) -> Result<MarketHost, MarketStartupError> {
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
    let identity = if let Some(value) = instance.as_ref() {
        InstanceIdentity::new(workspace.id(), value.launch_id(), value.instance_id())
    } else {
        InstanceIdentity::unscoped(workspace.id())
    }
    .map_err(MarketStartupError::new)?;
    let view_root = instance
        .as_ref()
        .map(|value| value.snapshot(&[]))
        .transpose()
        .map_err(MarketStartupError::new)?
        .unwrap_or_else(|| workspace.paths().snapshots_root());
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
    let health_path = instance
        .as_ref()
        .map(|value| value.service_health("market"))
        .transpose()
        .map_err(MarketStartupError::new)?
        .or_else(|| workspace.health_file("market").ok());
    let market_config = if profile.scope != MarketRuntimeScope::Replay {
        MarketCompositionConfig::load(&workspace).map_err(MarketStartupError::new)?
    } else {
        MarketCompositionConfig::default()
    };
    let reference_client = (profile.scope != MarketRuntimeScope::Replay)
        .then(|| {
            reference_endpoint(&workspace, request.aeron_dir.as_deref())
                .map(kairos_reference_contract::ReferenceClient::connect)
        })
        .transpose()?;
    let initial_reference_snapshot = reference_client
        .as_ref()
        .map(read_reference_snapshot)
        .transpose()?;

    std::fs::create_dir_all(&view_root).map_err(MarketStartupError::new)?;
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
        },
        MarketRuntimeScope::Shared
        | MarketRuntimeScope::Instance
        | MarketRuntimeScope::Diagnostic => {},
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
    }

    let history = (!history_specs.is_empty())
        .then(|| spawn_jsonl_history(history_specs).map_err(MarketStartupError::new))
        .transpose()?;
    let mut system = kairos_conflux::ConfluxSystem::new();
    // A static replay resolves subscriptions from its explicit request and
    // remains independent of the live Reference/Aeron runtime. Live modes
    // install both client and event stream into Conflux so no watcher task
    // becomes a second Aeron owner.
    let reference_projection = if let Some(client) = reference_client {
        let key = "market-reference".to_owned();
        let events = client
            .events(profile.publication_queue_capacity.max(1))
            .map_err(|error| MarketStartupError::new(error.to_string()))?;
        system
            .install_reference_contract(key.clone(), client, events)
            .map_err(|error| MarketStartupError::new(error.to_string()))?;
        Some(crate::application::ReferenceProjectionConfig {
            client_key: key,
            interval: profile.reference_recovery_interval,
            projection: build_reference_projection(&market_config.sources),
        })
    } else {
        None
    };
    let source_plans = if profile.scope != MarketRuntimeScope::Replay {
        let credentials_root = workspace
            .existing_path(&["config", "credentials"], &["credentials"])
            .map_err(MarketStartupError::new)?;
        super::super::sources::install_connections(
            &mut system,
            &credentials_root,
            &market_config.sources,
        )
        .map_err(MarketStartupError::new)?
    } else {
        Vec::new()
    };
    application
        .configure_conflux(
            profile.freshness_check_interval,
            profile.freshness_max_age,
            profile.shutdown_timeout,
            identity,
            source_plans,
            history,
            reference_projection,
        )
        .map_err(MarketStartupError::new)?;
    application
        .configure_view_publication(view_root, VIEW_SLOT_SIZE)
        .map_err(MarketStartupError::new)?;
    let event_endpoint = kairos_market_contract::AeronEndpoint::new(
        request.aeron_dir,
        kairos_conflux::DEFAULT_AERON_CHANNEL,
        kairos_conflux::output_stream_ids::MARKET_EVENTS,
    )
    .map_err(MarketStartupError::new)?;
    system
        .outputs()
        .aeron
        .declare(
            "market-events".to_owned(),
            AeronOutputDeclaration {
                endpoint: event_endpoint,
                revision: 1,
            },
        )
        .map_err(MarketStartupError::new)?;
    let _ = event_socket_path;
    Ok(MarketHost::new(
        application,
        system,
        socket_path,
        health_path,
        process_lock,
    ))
}
