use std::path::PathBuf;

use crate::domain::CapitalGroupConfig;
use crate::services::{actor::CapitalActor, persistence::JournalCapitalStore};
use crate::{CapitalApplication, CapitalTransferProcess};

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

pub fn compose_capital_transfer_process<C>(
    config: CapitalGroupConfig,
    connection: C,
) -> Result<CapitalTransferProcess<C>, String>
where
    C: kairos_integration::AssetTransferCommand + kairos_integration::AssetTransferStatusQuery,
{
    let group_id = config.capital_group_id.clone();
    let environment = config.environment.clone();
    let application = compose_capital_application(config);
    CapitalTransferProcess::new(application, connection, group_id, environment)
        .map_err(|error| error.to_string())
}

pub fn compose_persistent_capital_transfer_process<C>(
    config: CapitalGroupConfig,
    state_path: PathBuf,
    connection: C,
) -> Result<CapitalTransferProcess<C>, String>
where
    C: kairos_integration::AssetTransferCommand + kairos_integration::AssetTransferStatusQuery,
{
    let group_id = config.capital_group_id.clone();
    let environment = config.environment.clone();
    let application = compose_persistent_capital_application(config, state_path)?;
    CapitalTransferProcess::new(application, connection, group_id, environment)
        .map_err(|error| error.to_string())
}
