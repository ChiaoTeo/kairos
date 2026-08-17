//! Reference business boundary.

#[cfg(test)]
extern crate self as kairos_reference;

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::{
    CashDividendDatasetRecord, CashDividendDatasetRequest, CashDividendDatasetResult,
    CashDividendInput, LifecycleQuery, MarketQuery, OptionContractDatasetRecord,
    OptionContractInput, OptionContractSnapshotRequest, OptionContractSnapshotResult,
    ReferenceApplication, ReferenceCurrentView, ReferenceDatasetApplication, ReferenceKind,
    ReferenceQuery, ReferenceReadModel, ReferenceRecord, ReferenceRefreshResult,
};
