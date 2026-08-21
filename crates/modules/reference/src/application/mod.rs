//! Public reference use-case boundary.

mod app;
mod commands;
mod conflux;
mod queries;

#[cfg(test)]
mod tests;

kairos_reference_contract::reference_control_rpc_conflux_actor! {
    pub trait ReferenceRpcActor;
    service ReferenceRpcService;
}

pub use app::{
    ReferenceApplication, ReferencePublication, ReferenceReadModel, ReferenceRefreshResult,
};
pub use commands::{UpsertAssetCommand, UpsertInstrumentCommand, UpsertListingCommand};
pub use queries::{LifecycleQuery, MarketQuery, ReferenceKind, ReferenceQuery, ReferenceRecord};
