//! Public reference use-case boundary.

mod app;
pub mod control;
mod datasets;
mod queries;

#[cfg(test)]
mod tests;

pub use app::{
    ReferenceApplication, ReferenceCurrentView, ReferenceReadModel, ReferenceRefreshResult,
};
pub use datasets::{
    CashDividendDatasetRecord, CashDividendDatasetRequest, CashDividendDatasetResult,
    CashDividendInput, OptionContractDatasetRecord, OptionContractInput,
    OptionContractSnapshotRequest, OptionContractSnapshotResult, ReferenceDatasetApplication,
};
pub use queries::{LifecycleQuery, MarketQuery, ReferenceKind, ReferenceQuery, ReferenceRecord};
