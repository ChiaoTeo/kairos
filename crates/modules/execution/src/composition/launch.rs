//! Assembly of the Execution application, concrete resources, and Conflux host.

use std::path::PathBuf;

use super::{
    ExecutionConnectionOptions, ExecutionHost, ExecutionWriterFence, SimulatedAccountSettlement,
    SqlxExecutionAudit, SqlxExecutionStore, compose_order_entry, configure_execution_dependencies,
    load_execution_routes_from_reference_markets,
};
use crate::application::ExecutionApplication;
use crate::application::core::ExecutionApplicationWiring;
use crate::services::audit::ExecutionAudit;

pub struct ExecutionHostConfig {
    pub actor_id: String,
    pub route_options: Vec<ExecutionConnectionOptions>,
    pub writer_fences: Vec<ExecutionWriterFence>,
    pub state_path: PathBuf,
    pub audit_path: PathBuf,
    pub reference_database: PathBuf,
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

    if config.reference_database.exists() {
        for (access_id, participant_instrument) in load_execution_routes_from_reference_markets(
            &config.reference_database,
            &config.route_options,
        )? {
            application.configure_execution_route(access_id, participant_instrument);
        }
    } else if !config.simulated {
        return Err(format!(
            "live Execution requires canonical Reference markets: {}",
            config.reference_database.display()
        )
        .into());
    }

    configure_execution_dependencies(
        &mut application,
        &config.manifest_path,
        config.backtest,
        128,
    )?;
    application.configure_live_trading(!config.simulated, config.confirm_live);
    application.recover_risk_reservations()?;

    let settlement = config
        .simulated
        .then(|| SimulatedAccountSettlement::from_manifest(&config.manifest_path))
        .transpose()?;
    application.configure_conflux(
        plans,
        config.writer_fences,
        config.transport_identity,
        config.view_root,
        4 * 1024 * 1024,
        ExecutionAudit::from(audit),
        settlement,
    )?;
    let event_endpoint = kairos_execution_contract::AeronEndpoint::from_parts(
        config.aeron_dir.as_deref(),
        config.aeron_channel.clone(),
        config.execution_events_stream_id,
    )?;
    let publisher = kairos_execution_contract::ExecutionEventPublisher::connect(&event_endpoint)?;
    system
        .execution_event_publishers
        .ensure_with("execution-events".to_owned(), 1, || publisher)?;

    let _ = config.source_id;
    Ok(ExecutionHost::new(application, system, config.socket_path))
}
