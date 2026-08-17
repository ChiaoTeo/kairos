//! Complete assembly of the reusable Execution process.

use super::{
    compose_execution_routes, configure_execution_dependencies,
    load_execution_routes_from_reference_markets, AeronExecutionEventPublisher,
    ExecutionAsyncEventSource, ExecutionAsyncOrderEntryRoutes, ExecutionAsyncOrderQueryRoutes,
    ExecutionConnectionOptions, ExecutionSimulator, ExecutionWriterFence,
    SharedExecutionSnapshotPublisher, SharedIntentSnapshotPublisher, SimulatedAccountSettlement,
    SimulationConfig, SqlxExecutionAudit, SqlxExecutionStore,
};
use crate::application::core::ExecutionApplicationWiring;
use crate::application::{ExecutionApplication, ExecutionProcess};
use std::path::PathBuf;

type InnerExecutionProcess = ExecutionProcess<
    ExecutionAsyncOrderEntryRoutes,
    ExecutionAsyncOrderQueryRoutes,
    ExecutionAsyncEventSource,
>;

/// A fully assembled process. Its only public operation is lifecycle control;
/// concrete gateways, stores, publishers, and services remain encapsulated.
pub struct ComposedExecutionProcess {
    inner: InnerExecutionProcess,
}

impl ComposedExecutionProcess {
    pub async fn run(self) -> Result<(), Box<dyn std::error::Error>> {
        self.inner.run().await
    }
}

/// Deployment inputs resolved by the Execution binary and owned by
/// composition. No Integration connection, persistence implementation, or
/// private service crosses the public Application boundary.
pub struct ExecutionProcessConfig {
    pub actor_id: String,
    pub route_options: Vec<ExecutionConnectionOptions>,
    pub writer_fences: Vec<ExecutionWriterFence>,
    pub state_path: PathBuf,
    pub audit_path: PathBuf,
    pub reference_database: PathBuf,
    pub manifest_path: PathBuf,
    pub socket_path: PathBuf,
    pub execution_snapshot_path: PathBuf,
    pub intent_snapshot_path: PathBuf,
    pub transport_identity: kairos_protocol::InstanceIdentity,
    pub source_id: String,
    pub simulated: bool,
    pub backtest: bool,
    pub confirm_live: bool,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub execution_events_stream_id: i32,
}

pub fn compose_execution_process(
    config: ExecutionProcessConfig,
) -> Result<ComposedExecutionProcess, Box<dyn std::error::Error>> {
    let mut connections = compose_execution_routes(&config.route_options)?;
    if !config.simulated {
        connections
            .async_order_entry
            .as_mut()
            .ok_or("live Execution requires an async order-entry gateway")?
            .install_writer_fences(config.writer_fences)?;
    }

    tracing::info!(
        event = "integrations_composed",
        component = "execution",
        route_count = config.route_options.len(),
        "execution integrations composed"
    );

    let state_store = tokio::task::block_in_place(|| SqlxExecutionStore::new(config.state_path))?;
    let audit_store = tokio::task::block_in_place(|| SqlxExecutionAudit::new(config.audit_path))?;
    let mut application = ExecutionApplication::assemble(
        config.actor_id,
        ExecutionApplicationWiring {
            order_entry_gateway: connections.order_entry,
            order_query_gateway: connections.order_query,
            legacy_execution_stream: connections.execution_stream,
            state_store: Some(Box::new(state_store)),
        },
    )?;

    if config.reference_database.exists() {
        for (access_id, provider_instrument) in load_execution_routes_from_reference_markets(
            &config.reference_database,
            &config.route_options,
        )? {
            application.configure_execution_route(access_id, provider_instrument);
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

    let process = ExecutionProcess::with_audit(application, config.socket_path, audit_store)
        .with_async_order_entry(connections.async_order_entry)
        .with_async_order_query(connections.async_order_query)
        .with_async_execution_routes(connections.async_execution_streams);
    let process = if config.simulated {
        process
            .with_simulator(ExecutionSimulator::new(SimulationConfig::default())?)
            .with_simulated_account_settlement(SimulatedAccountSettlement::from_manifest(
                &config.manifest_path,
            )?)
    } else {
        process
    };

    let inner = process
        .with_snapshot_publisher(SharedExecutionSnapshotPublisher::create_with_identity(
            config.execution_snapshot_path,
            32 * 1024 * 1024,
            config.source_id.clone(),
            config.transport_identity.clone(),
        )?)
        .with_event_publisher(AeronExecutionEventPublisher::connect(
            config.aeron_dir.as_deref(),
            &config.aeron_channel,
            config.execution_events_stream_id,
            config.source_id.clone(),
            config.transport_identity.clone(),
        )?)
        .with_intent_snapshot_publisher(SharedIntentSnapshotPublisher::create_with_identity(
            config.intent_snapshot_path,
            1024 * 1024,
            config.source_id,
            config.transport_identity,
        )?);
    Ok(ComposedExecutionProcess { inner })
}
