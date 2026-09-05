//! Assembly of the Execution application, concrete resources, and Conflux host.

use std::path::PathBuf;
use std::time::Duration;

use kairos_conflux::{
    AeronOutputDeclaration, Conflux, ConfluxConfig, IndexedEnvironmentOptions,
    IndexedOutputDeclaration, JsonRpcRuntimeConfig,
};
use kairos_execution_contract::{
    EXECUTION_MAP_SIZE, ExecutionControlRpcServer, execution_indexed_environment_path,
    execution_indexed_identity,
};

use super::{
    ExecutionConnectionOptions, ExecutionHost, ExecutionWriterFence, SimulatedAccountSettlement,
    SqlxExecutionAudit, SqlxExecutionStore, compose_order_entry, configure_execution_dependencies,
    load_execution_routes_from_reference_markets,
};
use crate::application::core::ExecutionApplicationWiring;
use crate::application::{ExecutionApplication, ExecutionRpcService};
use crate::services::audit::ExecutionAudit;

pub struct ExecutionHostConfig {
    pub actor_id: String,
    pub route_options: Vec<ExecutionConnectionOptions>,
    pub writer_fences: Vec<ExecutionWriterFence>,
    pub state_path: PathBuf,
    pub audit_path: PathBuf,
    pub reference_connection: kairos_reference_contract::ReferenceConnection,
    pub manifest_path: PathBuf,
    pub socket_path: PathBuf,
    pub view_root: PathBuf,
    pub transport_identity: kairos_primitives::runtime::InstanceIdentity,
    pub source_id: String,
    pub simulated: bool,
    pub backtest: bool,
    pub confirm_live: bool,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub execution_events_stream_id: i32,
}

pub fn build_execution_host(
    config: ExecutionHostConfig,
) -> Result<ExecutionHost, Box<dyn std::error::Error>> {
    let mut system = kairos_conflux::ConfluxSystem::new();
    let (plans, descriptors) =
        super::connections::install_execution_connections(&mut system, &config.route_options)?;
    let simulated_entry = if config.simulated {
        config
            .route_options
            .first()
            .map(compose_order_entry)
            .transpose()?
    } else {
        None
    };

    tracing::info!(
        event = "integrations_composed",
        component = "execution",
        route_count = descriptors.len(),
        "execution integrations installed in Conflux"
    );

    let state_store = tokio::task::block_in_place(|| SqlxExecutionStore::new(config.state_path))?;
    let audit = tokio::task::block_in_place(|| SqlxExecutionAudit::new(config.audit_path))?;
    let mut application = ExecutionApplication::assemble(
        config.actor_id,
        ExecutionApplicationWiring {
            order_entry_gateway: simulated_entry,
            order_query_gateway: None,
            state_store: Some(Box::new(state_store)),
        },
    )?;

    let reference_database = config.reference_connection.database.clone();
    let mut reference_catalog = None;
    if reference_database.exists() {
        let catalog = kairos_reference_contract::ReferenceCatalog::open(&reference_database)?;
        let page = load_reference_route_facts(&catalog, &config.route_options)?;
        for (access_id, participant_instrument) in
            load_execution_routes_from_reference_markets(&page, &config.route_options)?
        {
            application.configure_execution_route(access_id, participant_instrument);
        }
        reference_catalog = Some(catalog);
    } else if !config.simulated {
        return Err(format!(
            "live Execution requires canonical Reference markets: {}",
            reference_database.display()
        )
        .into());
    }

    configure_execution_dependencies(
        &mut application,
        &mut system,
        &config.manifest_path,
        reference_catalog,
        config.backtest,
        128,
    )?;
    application.configure_live_trading(!config.simulated, config.confirm_live);
    application.recover_risk_reservations()?;

    let settlement = config
        .simulated
        .then(|| SimulatedAccountSettlement::from_manifest(&mut system, &config.manifest_path))
        .transpose()?;
    let transport_identity = config.transport_identity.clone();
    application.configure_conflux(
        plans,
        config.writer_fences,
        transport_identity.clone(),
        ExecutionAudit::from(audit),
        settlement,
    )?;
    application.configure_wall_clock_business_time(!config.backtest);
    let event_endpoint = kairos_execution_contract::AeronEndpoint::from_parts(
        config.aeron_dir.as_deref(),
        config.aeron_channel.clone(),
        config.execution_events_stream_id,
    )?;
    let view_path = execution_indexed_environment_path(&config.view_root, &transport_identity)?;
    let view_options = IndexedEnvironmentOptions::new(view_path, EXECUTION_MAP_SIZE)?;
    system.outputs().indexed.declare(
        "execution-current",
        IndexedOutputDeclaration {
            options: view_options,
            identity: execution_indexed_identity(
                &transport_identity,
                application.conflux_producer_incarnation(),
            ),
            revision: 1,
        },
    )?;
    system.outputs().aeron.declare(
        "execution-events".to_owned(),
        AeronOutputDeclaration {
            endpoint: event_endpoint,
            revision: 1,
        },
    )?;

    let _ = config.source_id;
    let (conflux, handle) = Conflux::new(
        application,
        system,
        ConfluxConfig {
            ingress_capacity: 1_024,
            ..ConfluxConfig::default()
        },
    )?;
    let invocation = handle.rpc_actor_invocation(Duration::from_secs(30));
    let methods = ExecutionRpcService::<ExecutionApplication>::new(invocation).into_rpc();
    Ok(conflux.with_json_rpc(
        handle,
        methods,
        JsonRpcRuntimeConfig::uds(config.socket_path),
    ))
}

fn load_reference_route_facts(
    catalog: &kairos_reference_contract::ReferenceCatalog,
    configured_routes: &[crate::composition::connections::ExecutionConnectionOptions],
) -> Result<kairos_reference_contract::MarketSearchResponse, Box<dyn std::error::Error>> {
    let session = catalog.read_session()?;
    let mut response = kairos_reference_contract::MarketSearchResponse {
        evidence: kairos_reference_contract::ReferenceQueryEvidence {
            watermark: session.watermark(),
            conclusion: kairos_reference_contract::ReferenceKnowledgeConclusion::Found,
            ..Default::default()
        },
        ..Default::default()
    };
    for route in configured_routes {
        if route.instruments.is_empty() {
            let venue_key = route.broker_id.trim().to_ascii_lowercase();
            let venue_key = if venue_key == "okex" {
                "okx"
            } else {
                &venue_key
            };
            if !matches!(venue_key, "binance" | "okx" | "hyperliquid") {
                return Err(format!(
                    "Execution route {} must declare instrument addresses; broker {} is not a canonical execution venue",
                    route.route_id, route.broker_id
                )
                .into());
            }
            let page = session.search_venue_markets(
                &kairos_reference_contract::VenueMarketSearchQuery {
                    execution_venue_id: Some(kairos_primitives::reference::VenueId::new(format!(
                        "venue:{venue_key}"
                    ))?),
                    active_only: true,
                    page: kairos_reference_contract::ReferencePage {
                        limit: Some(1_000),
                        offset: 0,
                    },
                    ..Default::default()
                },
            )?;
            if page.next_cursor.is_some() {
                return Err(format!(
                    "Execution route {} resolves to more than 1000 Reference markets; declare explicit instrument addresses",
                    route.route_id
                )
                .into());
            }
            response.markets.extend(page.markets);
            response.instruments.extend(page.instruments);
            continue;
        }
        for address in &route.instruments {
            let instruments =
                session.search_instruments(&kairos_reference_contract::InstrumentSearchQuery {
                    instrument_ids: Some(vec![kairos_primitives::reference::InstrumentId::new(
                        &address.instrument_id,
                    )?]),
                    page: kairos_reference_contract::ReferencePage {
                        limit: Some(2),
                        offset: 0,
                    },
                    ..Default::default()
                })?;
            let [instrument] = instruments.instruments.as_slice() else {
                return Err(format!(
                    "Execution route {} references missing or ambiguous instrument {}",
                    route.route_id, address.instrument_id
                )
                .into());
            };
            response
                .instruments
                .insert(instrument.instrument_id.clone(), instrument.clone());
            if let Some(market_id) = address.destination_market_id.as_deref() {
                let resolved =
                    session.resolve_market(&kairos_reference_contract::MarketResolutionQuery {
                        market_id: Some(kairos_primitives::reference::MarketId::new(market_id)?),
                        instrument_id: Some(instrument.instrument_id.clone()),
                        active_only: true,
                        ..Default::default()
                    })?;
                let Some(resolution) = resolved.resolution else {
                    return Err(format!(
                        "Execution route {} destination {market_id} is not a usable Reference market; conclusion={:?}",
                        route.route_id, resolved.evidence.conclusion
                    )
                    .into());
                };
                response.markets.push(resolution.market);
            }
        }
    }
    response
        .markets
        .sort_by(|left, right| left.market_id.cmp(&right.market_id));
    response
        .markets
        .dedup_by(|left, right| left.market_id == right.market_id);
    Ok(response)
}
