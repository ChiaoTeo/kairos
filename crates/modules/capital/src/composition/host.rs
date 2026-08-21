use std::path::PathBuf;
use std::time::Duration;

use kairos_capital_contract::{CapitalControlRpcServer, CapitalViewKey, capital_view_path};
use kairos_conflux::{
    AeronOutputDeclaration, Conflux, ConfluxConfig, ConfluxSystem, JsonRpcConfluxRuntime,
    JsonRpcRuntimeConfig, MmapOutputDeclaration,
};

use super::CapitalIntegrationConnections;
use crate::application::CapitalRpcService;
use crate::{CapitalConfluxConfig, CapitalProcess};

pub type CapitalHost = JsonRpcConfluxRuntime<CapitalProcess<CapitalIntegrationConnections>>;

pub struct CapitalHostConfig {
    pub runtime: CapitalProcess<CapitalIntegrationConnections>,
    pub system: ConfluxSystem,
    pub conflux: CapitalConfluxConfig,
    pub socket_path: PathBuf,
    pub health_file: Option<PathBuf>,
    pub aeron_dir: Option<String>,
    pub event_channel: String,
    pub event_stream_id: i32,
    pub ingress_capacity: usize,
}

/// Assemble the Capital Actor, its typed contract outputs, and its Conflux host.
pub fn build_capital_host(mut config: CapitalHostConfig) -> Result<CapitalHost, String> {
    let group_id = config.runtime.application().snapshot().capital_group_id;
    let view_key = CapitalViewKey::current(group_id.to_string());
    let view_resource_key = view_key.canonical_key();
    let view_path = capital_view_path(&config.conflux.snapshot_root, &view_key)
        .map_err(|error| error.to_string())?;
    let event_endpoint = kairos_capital_contract::AeronEndpoint::from_parts(
        config.aeron_dir.as_deref(),
        config.event_channel,
        config.event_stream_id,
    )
    .map_err(|error| error.to_string())?;

    config
        .runtime
        .configure_conflux(config.conflux)
        .map_err(|error| error.to_string())?;
    let mut system = config.system;
    system
        .outputs()
        .mmap
        .declare(
            view_resource_key,
            MmapOutputDeclaration {
                path: view_path,
                slot_capacity: 4 * 1024 * 1024,
                revision: 1,
            },
        )
        .map_err(|error| error.to_string())?;
    system
        .outputs()
        .aeron
        .declare(
            "capital-events".to_owned(),
            AeronOutputDeclaration {
                endpoint: event_endpoint,
                revision: 1,
            },
        )
        .map_err(|error| error.to_string())?;

    let (conflux, handle) = Conflux::new(
        config.runtime,
        system,
        ConfluxConfig {
            ingress_capacity: config.ingress_capacity,
            ..ConfluxConfig::default()
        },
    )
    .map_err(|error| error.to_string())?;
    let invocation = handle.rpc_actor_invocation(Duration::from_secs(30));
    let methods =
        CapitalRpcService::<CapitalProcess<CapitalIntegrationConnections>>::new(invocation)
            .into_rpc();
    let control =
        JsonRpcRuntimeConfig::uds(config.socket_path).with_health_file(config.health_file);
    Ok(conflux.with_json_rpc(handle, methods, control))
}
