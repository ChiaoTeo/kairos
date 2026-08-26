//! Assembly of the Execution application, concrete resources, and Conflux host.

use std::path::PathBuf;
use std::time::Duration;

use kairos_conflux::{
    AeronOutputDeclaration, Conflux, ConfluxConfig, JsonRpcRuntimeConfig, MmapOutputDeclaration,
};
use kairos_execution_contract::{
    ExecutionControlRpcServer, ExecutionViewKey, ExecutionViewKind, ExecutionViewPublisher,
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

    let reference_key = "execution-reference";
    let reference_database = config.reference_connection.database.clone();
    let mut reference_snapshot = None;
    if reference_database.exists() {
        system
            .install_reference_connection(reference_key, config.reference_connection, 1)
            .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
        let snapshot = system
            .reference_execution_snapshot(reference_key)
            .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
        for (access_id, participant_instrument) in
            load_execution_routes_from_reference_markets(&snapshot, &config.route_options)?
        {
            application.configure_execution_route(access_id, participant_instrument);
        }
        reference_snapshot = Some(snapshot);
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
        reference_snapshot,
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
    for kind in [ExecutionViewKind::CurrentExecution] {
        let key = ExecutionViewKey::from_identity(&transport_identity, kind);
        let resource_key = key.canonical_key();
        let path = ExecutionViewPublisher::resolved_path(&config.view_root, &key)?;
        system.outputs().mmap.declare(
            resource_key,
            MmapOutputDeclaration {
                path,
                slot_capacity: 4 * 1024 * 1024,
                revision: 1,
            },
        )?;
    }
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
