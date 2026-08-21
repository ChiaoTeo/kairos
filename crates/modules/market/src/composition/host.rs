use std::path::PathBuf;

use kairos_conflux::{Conflux, ConfluxConfig, ConfluxSystem, JsonRpcRuntimeConfig};
use kairos_market_contract::MarketControlRpcServer;

use crate::MarketApplication;
use crate::application::MarketRpcService;

/// Retains the Workspace process lease while the framework-owned host runs.
/// Listener, request, readiness, health, and shutdown lifecycle all belong to
/// Conflux.
pub struct MarketHost {
    application: MarketApplication,
    system: ConfluxSystem,
    socket: PathBuf,
    health: Option<PathBuf>,
    _process_lock: kairos_workspace::workspace::WorkspaceProcessLock,
}

impl MarketHost {
    pub(crate) fn new(
        application: MarketApplication,
        system: ConfluxSystem,
        socket: PathBuf,
        health: Option<PathBuf>,
        process_lock: kairos_workspace::workspace::WorkspaceProcessLock,
    ) -> Self {
        Self {
            application,
            system,
            socket,
            health,
            _process_lock: process_lock,
        }
    }

    pub async fn run(self) -> Result<(), Box<dyn std::error::Error>> {
        let (conflux, handle) = Conflux::new(
            self.application,
            self.system,
            ConfluxConfig {
                ingress_capacity: 1_024,
                ..ConfluxConfig::default()
            },
        )?;
        let invocation = handle.rpc_actor_invocation(std::time::Duration::from_secs(30));
        let methods = MarketRpcService::<MarketApplication>::new(invocation).into_rpc();
        let outcome = conflux
            .with_json_rpc(
                handle,
                methods,
                JsonRpcRuntimeConfig::uds(self.socket).with_health_file(self.health),
            )
            .run()
            .await?;
        tracing::info!(event = "process_stopped", component = "market", phase = ?outcome.phase, discarded_inputs = outcome.discarded_inputs, "Market Conflux process stopped");
        Ok(())
    }
}
