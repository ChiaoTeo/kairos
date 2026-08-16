use std::fmt;

use kairos_protocol::InstanceIdentity;
use kairos_workspace::Workspace;

use crate::application::{load_replay_events, MarketProcessSettings};
use crate::services::history::{HistoryCollectionSpec, JsonlMarketHistoryRecorder};
use crate::services::sources::load_actor_checkpoint;
use crate::{MarketApplication, MarketDescriptor, MarketProcess, SubscriptionId};

use super::{
    attach_replay_source_with_policy, ConfiguredMarketSourceActivator, MarketCompositionConfig,
    MarketProcessRequest, MarketRuntimeProfile, MarketRuntimeScope, MmapMarketSnapshotPublisher,
};

const SNAPSHOT_SLOT_SIZE: usize = 4_194_304;
const MAX_DYNAMIC_MEMBERS: usize = 10_000;

fn collection_market_descriptor(
    reference_database: &std::path::Path,
    name: &str,
    collection: &super::config::MarketCollectionConfig,
) -> Result<MarketDescriptor, MarketStartupError> {
    let market_id = collection.market_id.as_deref().ok_or_else(|| {
        MarketStartupError::new(format!(
            "Market collection {name} requires canonical market_id"
        ))
    })?;
    let reader = kairos_reference_contract::ReferenceSqliteReader::open(reference_database)
        .map_err(MarketStartupError::new)?;
    let market = reader
        .market(market_id)
        .map_err(MarketStartupError::new)?
        .ok_or_else(|| {
            MarketStartupError::new(format!(
                "Market collection {name} references missing market {market_id}"
            ))
        })?;
    let accesses = reader
        .market_data_accesses(&kairos_reference_contract::SqliteMarketDataAccessQuery {
            market_id: Some(market_id.to_owned()),
            statuses: vec!["active".into(), "trading".into()],
            limit: 100,
            ..Default::default()
        })
        .map_err(MarketStartupError::new)?;
    let selected = accesses
        .iter()
        .filter(|access| {
            collection
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
    let mut descriptor = MarketDescriptor::new(
        market.market_id,
        market.instrument_id,
        market.exchange_id,
        market.market_type,
        market.source_symbol,
    )
    .map_err(MarketStartupError::new)?;
    descriptor.asset_type = market
        .asset_type
        .map(|value| value.parse())
        .transpose()
        .map_err(MarketStartupError::new)?;
    descriptor.underlying_instrument_id = market
        .underlying_instrument_id
        .map(kairos_primitives::InstrumentId::new)
        .transpose()
        .map_err(MarketStartupError::new)?;
    descriptor.market_data_access_id = Some(access.access_id.clone());
    descriptor.market_data_provider_id = Some(
        kairos_primitives::ProviderId::new(access.provider_id.clone())
            .map_err(MarketStartupError::new)?,
    );
    descriptor.market_data_provider_product = Some(
        kairos_primitives::ProviderProductCode::new(access.provider_product.clone())
            .map_err(MarketStartupError::new)?,
    );
    descriptor.provider_symbol = Some(
        kairos_primitives::ProviderSymbol::new(access.provider_symbol.clone())
            .map_err(MarketStartupError::new)?,
    );
    if let Some(source_id) = &collection.source_id {
        descriptor = descriptor
            .with_source(source_id.clone())
            .map_err(MarketStartupError::new)?;
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
    let reference_database = workspace
        .child(&["reference", "reference.sqlite"])
        .map_err(MarketStartupError::new)?;

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
    let restored_snapshot = replay_checkpoint_path
        .as_deref()
        .map(load_actor_checkpoint)
        .transpose()
        .map_err(MarketStartupError::new)?
        .flatten();
    let actor_id = format!("market:{}", profile.name);
    let mut application = match restored_snapshot {
        Some(snapshot) => MarketApplication::restore_with_source_capacity(
            snapshot,
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
            let descriptor = collection_market_descriptor(&reference_database, name, collection)?;
            let subscription_id = SubscriptionId::new(format!("collection:{name}"))
                .map_err(MarketStartupError::new)?;
            let owner_id = format!("collection:{name}");
            application
                .subscribe_static_with_selectors(
                    subscription_id,
                    owner_id,
                    descriptor.clone(),
                    collection.selectors.clone(),
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

    let publisher = MmapMarketSnapshotPublisher::create_with_identity(
        &snapshot_path,
        SNAPSHOT_SLOT_SIZE,
        format!("market:{}", profile.name),
        identity.clone(),
    )
    .map_err(MarketStartupError::new)?;
    let settings = MarketProcessSettings {
        snapshot_interval: profile.snapshot_interval,
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
    )
    .map_err(MarketStartupError::new)?;
    if !history_specs.is_empty() {
        process = process.with_history_recorder(
            JsonlMarketHistoryRecorder::spawn(history_specs).map_err(MarketStartupError::new)?,
        );
    }
    // A static replay resolves subscriptions from its explicit request and
    // must remain independent of the live Reference/Aeron runtime.
    if profile.scope != MarketRuntimeScope::Replay {
        process = process.with_reference_database(reference_database);
    }
    if profile.scope != MarketRuntimeScope::Replay {
        process = process.with_aeron_event_publisher(
            kairos_transport::AeronBytePublisher::connect(
                std::env::var("AERON_DIR").ok().as_deref(),
                kairos_transport::DEFAULT_CHANNEL,
                kairos_transport::stream_ids::MARKET_EVENTS,
            )
            .map_err(MarketStartupError::new)?,
        );
    }
    Ok(process.with_lifecycle_guard(process_lock))
}
