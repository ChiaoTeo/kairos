use std::path::PathBuf;

use crate::domain::CapitalGroupConfig;
use crate::services::actor::CapitalActor;
use crate::services::persistence::JournalCapitalStore;
use crate::{CapitalApplication, CapitalProcess};

mod connections;
mod host;
pub use connections::{
    CapitalConnectionAccount, CapitalIntegrationConnections,
    compose_capital_integration_connections, validate_capital_transfer_product,
};
pub use host::{CapitalHost, CapitalHostConfig, build_capital_host};

pub fn compose_capital_application(config: CapitalGroupConfig) -> CapitalApplication {
    CapitalApplication::new(
        CapitalActor::new(config, None).expect("in-memory Capital Actor construction cannot fail"),
    )
}

pub fn compose_persistent_capital_application(
    config: CapitalGroupConfig,
    state_path: PathBuf,
) -> Result<CapitalApplication, String> {
    CapitalActor::new(config, Some(JournalCapitalStore::new(state_path)))
        .map(CapitalApplication::new)
}

pub fn compose_capital_process<C>(
    config: CapitalGroupConfig,
    connection: C,
) -> Result<CapitalProcess<C>, String>
where
    C: kairos_conflux::AssetTransferCommand + kairos_conflux::AssetTransferStatusQuery,
{
    let group_id = config.capital_group_id.clone();
    let environment = config.environment.clone();
    let application = compose_capital_application(config);
    CapitalProcess::new(application, connection, group_id, environment)
        .map_err(|error| error.to_string())
}

pub fn compose_persistent_capital_process<C>(
    config: CapitalGroupConfig,
    state_path: PathBuf,
    connection: C,
) -> Result<CapitalProcess<C>, String>
where
    C: kairos_conflux::AssetTransferCommand + kairos_conflux::AssetTransferStatusQuery,
{
    let group_id = config.capital_group_id.clone();
    let environment = config.environment.clone();
    let application = compose_persistent_capital_application(config, state_path)?;
    CapitalProcess::new(application, connection, group_id, environment)
        .map_err(|error| error.to_string())
}
