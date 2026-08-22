//! Public reference use-case boundary.

mod app;
mod cli;
mod commands;
mod conflux;
mod connected;
mod diagnostics;
mod publication;
mod queries;
mod read_model;
mod runtime;
mod runtime_status;
mod source_control;
mod startup;
mod tick;

#[cfg(test)]
mod tests;

kairos_reference_contract::reference_control_rpc_conflux_actor! {
    pub trait ReferenceRpcActor;
    service ReferenceRpcService;
}

pub use app::ReferenceApplication;
pub use cli::{
    CliReferenceApplication, ReferenceCatalogCollection, ReferenceCatalogListRequest,
    ReferenceMarketCatalogRequest, ReferenceOptionChainRequest,
};
pub use commands::{UpsertAssetCommand, UpsertInstrumentCommand, UpsertListingCommand};
pub use connected::ConnectedReferenceApplication;
pub use publication::ReferencePublication;
pub use queries::{LifecycleQuery, MarketQuery, ReferenceKind, ReferenceQuery, ReferenceRecord};
pub use read_model::ReferenceReadModel;
pub(crate) use runtime::{ReferenceApplicationPhase, ReferenceTickTrigger};
pub use tick::ReferenceRefreshResult;
