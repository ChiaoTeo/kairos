//! Concrete Execution assembly.
//!
//! Composition selects Integration capabilities, cross-business dependency
//! adapters, persistence, publication, and runtime modes. Business use cases
//! remain in `application`; provider and infrastructure choices remain here.

mod connections;
mod dependencies;
mod host;
mod launch;
mod persistence;

pub use connections::{
    ExecutionConnectionOptions, ExecutionInstrumentRoute, ExecutionWriterFence,
    SimulatedOrderEntry, compose_order_entry, load_execution_routes_from_reference_markets,
};
pub use dependencies::{configure_execution_dependencies, configure_simulated_risk};
pub use host::ExecutionHost;
pub use launch::{ExecutionHostConfig, build_execution_host};
pub use persistence::{FileExecutionStore, MemoryStateStore, SqlxExecutionStore};

pub use crate::services::audit::{MemoryExecutionAudit, SqlxExecutionAudit};
pub use crate::services::risk::{SimulatedRiskBehavior, SimulatedRiskReconciliation};
pub use crate::services::simulation::{
    ExecutionSimulator, SimulatedAccountSettlement, SimulationConfig, SimulationFill,
    SimulationOrder, SimulationOrderRequest, SimulationOrderStatus, SimulationResult,
};
