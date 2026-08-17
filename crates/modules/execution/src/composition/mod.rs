//! Concrete Execution assembly.
//!
//! Composition selects Integration capabilities, cross-business dependency
//! adapters, persistence, publication, and runtime modes. Business use cases
//! remain in `application`; provider and infrastructure choices remain here.

mod connections;
mod dependencies;
mod persistence;
mod process;
mod providers;

pub use crate::services::audit::{MemoryExecutionAudit, SqlxExecutionAudit};
pub use crate::services::publication::{
    AeronExecutionEventPublisher, SharedExecutionSnapshotPublisher, SharedIntentSnapshotPublisher,
};
pub use crate::services::risk::{SimulatedRiskBehavior, SimulatedRiskReconciliation};
pub use connections::{
    compose_direct_execution_connections, compose_execution_connections, compose_execution_routes,
    compose_execution_stream, compose_order_entry, compose_order_query,
    load_reference_execution_accesses, DirectExecutionConnections, DirectExecutionRuntime,
    ExecutionAsyncEventSource, ExecutionAsyncOrderEntry, ExecutionAsyncOrderEntryRoutes,
    ExecutionAsyncOrderQuery, ExecutionAsyncOrderQueryRoutes, ExecutionConnectionOptions,
    ExecutionConnections, ExecutionWriterFence, SimulatedOrderEntry,
};
pub use dependencies::{configure_execution_dependencies, configure_simulated_risk};
pub use persistence::{FileExecutionStore, MemoryStateStore, SqlxExecutionStore};
pub use process::{compose_execution_process, ComposedExecutionProcess, ExecutionProcessConfig};

pub use crate::services::simulation::{
    ExecutionSimulator, SimulatedAccountSettlement, SimulationConfig, SimulationFill,
    SimulationOrder, SimulationOrderRequest, SimulationOrderStatus, SimulationResult,
};
