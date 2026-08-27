use std::fmt;

use kairos_conflux::{AeronOutputDeclaration, IndexedEnvironmentOptions, IndexedOutputDeclaration};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_workspace::Workspace;

use super::super::reference::{build_market_universe_resolver, resolve_market_universe};
use super::super::{
    MarketCompositionConfig, MarketHost, MarketHostRequest, MarketRuntimeProfile,
    MarketRuntimeScope, attach_replay_source_with_policy,
};
use crate::application::load_replay_events;
use crate::composition::history::{HistoryCollectionSpec, spawn_jsonl_history};
use crate::services::source::load_replay_checkpoint;
use crate::{MarketApplication, ResolvedMarket, SubscriptionId};

const MAX_DYNAMIC_MEMBERS: usize = 10_000;

fn collection_market_descriptor(
    reference: &kairos_reference_contract::MarketReferenceSnapshot,
    sources: &std::collections::BTreeMap<String, super::super::config::MarketProviderBinding>,
    name: &str,
    collection: &super::super::config::MarketCollectionConfig,
) -> Result<ResolvedMarket, MarketStartupError> {
    if collection.market_id.is_some() == collection.instrument_id.is_some() {
        return Err(MarketStartupError::new(format!(
            "Market collection {name} requires exactly one of market_id or instrument_id"
        )));
    }
    let universe = resolve_market_universe(reference, sources)
        .map_err(MarketStartupError::new)?
        .markets;
    let mut descriptor =
        if let Some(market_id) = collection.market_id.as_deref() {
            universe
            .into_iter()
            .find(|market| market.market_id().is_some_and(|candidate| candidate == market_id))
            .ok_or_else(|| {
                MarketStartupError::new(format!(
                    "Market collection {name} references missing or unsupported Market {market_id}"
                ))
            })?
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
            let canonical = universe
            .iter()
            .find(|market| {
                market.instrument_id == instrument.instrument_id
                    && collection.provider.as_ref().is_none_or(|provider| {
                        market.runtime_routes.contains_key(provider)
                    })
            })
            .ok_or_else(|| {
                MarketStartupError::new(format!(
                    "Market collection {name} has no provider route for instrument {instrument_id}"
                ))
            })?;
            let binding = collection
                .provider
                .as_ref()
                .and_then(|provider| canonical.runtime_routes.get(provider))
                .or_else(|| canonical.runtime_routes.values().next())
                .cloned()
                .expect("resolved Market has a runtime binding");
            let mut value = ResolvedMarket::consolidated_reference(
                instrument.instrument_id.clone(),
                collection.network_id.clone(),
                instrument.instrument_type,
                binding,
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
    let provider = collection
        .provider
        .clone()
        .or_else(|| descriptor.runtime_routes.keys().next().cloned())
        .ok_or_else(|| {
            MarketStartupError::new(format!("Market collection {name} has no provider route"))
        })?;
    if !descriptor.retain_provider(&provider) {
        return Err(MarketStartupError::new(format!(
            "Market collection {name} cannot be served by provider {provider}"
        )));
    }
    Ok(descriptor)
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
    let mut system = kairos_conflux::ConfluxSystem::new();
    let reference_client_key = (profile.scope != MarketRuntimeScope::Replay)
        .then(|| {
            kairos_conflux::reference_connection_from_workspace(
                &workspace,
                request.aeron_dir.as_deref(),
            )
            .map_err(MarketStartupError::new)
        })
        .map(|endpoint| {
            let key = "market-reference".to_owned();
            system
                .install_reference_connection(
                    key.clone(),
                    endpoint?,
                    profile.publication_queue_capacity.max(1),
                )
                .map_err(|error| MarketStartupError::new(error.to_string()))?;
            Ok::<_, MarketStartupError>(key)
        })
        .transpose()?;
    let initial_reference_snapshot = reference_client_key
        .as_ref()
        .map(|key| {
            system
                .reference_market_snapshot(key)
                .map_err(MarketStartupError::new)
        })
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
                resolve_market_universe(snapshot, &market_config.providers)
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
            let descriptor = collection_market_descriptor(
                reference,
                &market_config.providers,
                name,
                collection,
            )?;
            let subscription_id = SubscriptionId::new(format!("collection:{name}"))
                .map_err(MarketStartupError::new)?;
            let owner_id = format!("collection:{name}");
            application
                .subscribe_static_with_selectors(
                    subscription_id,
                    owner_id,
                    descriptor.clone(),
                    collection
                        .observations
                        .iter()
                        .map(|value| crate::ObservationSelector::parse(value))
                        .collect::<Result<Vec<_>, _>>()
                        .map_err(MarketStartupError::new)?,
                )
                .map_err(MarketStartupError::new)?;
            history_specs.push(HistoryCollectionSpec {
                name: name.clone(),
                scope_key: descriptor.scope.key(),
                selectors: collection.observations.clone(),
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
    // A static replay resolves subscriptions from its explicit request and
    // remains independent of the live Reference/Aeron runtime. Live modes
    // use the Reference client held by Conflux so no watcher task becomes a
    // second Aeron owner and Market does not create a foreign module client.
    let reference_universe_sync = if let Some(key) = reference_client_key {
        Some(crate::application::ReferenceUniverseSyncConfig {
            client_key: key,
            interval: profile.reference_recovery_interval,
            resolver: build_market_universe_resolver(&market_config.providers),
        })
    } else {
        None
    };
    let source_plans = if profile.scope != MarketRuntimeScope::Replay {
        let credentials_root = workspace
            .existing_credentials_root()
            .map_err(MarketStartupError::new)?;
        super::super::sources::install_connections(
            &mut system,
            &credentials_root,
            &kairos_integration::composition::ProviderConnectionProfile::canonical_root(
                workspace.root(),
            ),
            &market_config.providers,
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
            reference_universe_sync,
        )
        .map_err(MarketStartupError::new)?;
    let (indexed_identity, producer_incarnation) = application.indexed_publication_identity();
    let indexed_path =
        kairos_market_contract::market_indexed_environment_path(&view_root, indexed_identity)
            .map_err(MarketStartupError::new)?;
    let declaration_identity =
        kairos_market_contract::market_indexed_identity(indexed_identity, producer_incarnation);
    system
        .outputs()
        .indexed
        .declare(
            "market-current",
            IndexedOutputDeclaration {
                options: IndexedEnvironmentOptions::new(
                    indexed_path,
                    kairos_market_contract::MARKET_MAP_SIZE,
                )
                .map_err(MarketStartupError::new)?,
                identity: declaration_identity,
                revision: 1,
            },
        )
        .map_err(MarketStartupError::new)?;
    if profile.scope != MarketRuntimeScope::Replay {
        let aeron_channel = request.aeron_channel.as_deref().ok_or_else(|| {
            MarketStartupError::new(
                "Market live event publication requires an explicit System event route",
            )
        })?;
        let event_endpoint = kairos_market_contract::AeronEndpoint::new(
            request.aeron_dir,
            aeron_channel,
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
    }
    let _ = event_socket_path;
    Ok(MarketHost::new(
        application,
        system,
        socket_path,
        health_path,
        process_lock,
    ))
}
