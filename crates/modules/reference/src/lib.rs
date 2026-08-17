//! Reference business boundary.

#[cfg(test)]
extern crate self as kairos_reference;

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::{
    LifecycleQuery, MarketQuery, ReferenceApplication, ReferenceKind, ReferencePublication,
    ReferenceQuery, ReferenceReadModel, ReferenceRecord, ReferenceRefreshResult,
    UpsertAssetCommand, UpsertInstrumentCommand, UpsertListingCommand,
};
