//! Concrete Execution assembly.
//!
//! Composition selects Integration capabilities, cross-business dependency
//! adapters, persistence, publication, and runtime modes. Business use cases
//! remain in `application`; provider and infrastructure choices remain here.

use std::path::PathBuf;

use kairos_primitives::runtime::InstanceIdentity;

use crate::application::ConnectedExecutionApplication;

mod connections;
mod dependencies;
mod direct;
mod launch;
mod persistence;

pub use connections::{
    ExecutionConnectionOptions, ExecutionInstrumentRoute, ExecutionVenueEnvironment,
    ExecutionWriterFence, SimulatedOrderEntry, compose_order_entry,
    load_execution_routes_from_reference_markets,
};
pub use dependencies::{configure_execution_dependencies, configure_simulated_risk};
pub use direct::compose_standalone_execution;
pub use launch::{ExecutionHostConfig, build_execution_host};
pub use persistence::{FileExecutionStore, MemoryStateStore, SqlxExecutionStore};

pub use crate::services::audit::{MemoryExecutionAudit, SqlxExecutionAudit};
pub use crate::services::risk::{SimulatedRiskBehavior, SimulatedRiskReconciliation};
pub use crate::services::simulation::{
    ExecutionSimulator, SimulatedAccountSettlement, SimulationConfig, SimulationFill,
    SimulationOrder, SimulationOrderRequest, SimulationOrderStatus, SimulationResult,
};

pub type ExecutionHost =
    kairos_conflux::JsonRpcConfluxRuntime<crate::application::ExecutionApplication>;

/// Select and install the concrete runtime client used by the connected CLI.
pub fn connect_execution_application(
    socket: PathBuf,
    view_root: Option<PathBuf>,
    identity: InstanceIdentity,
) -> Result<ConnectedExecutionApplication, Box<dyn std::error::Error>> {
    let mut system = kairos_conflux::ConfluxSystem::new();
    system.install_execution_connection("execution", socket, view_root)?;
    let client = system
        .execution_client("execution")
        .ok_or("managed Execution client is missing: execution")?;
    Ok(ConnectedExecutionApplication::new(client, identity))
}
