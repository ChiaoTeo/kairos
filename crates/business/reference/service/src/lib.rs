//! Reference business boundary.

#[cfg(test)]
extern crate self as kairos_reference;

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::{
    LifecycleQuery, MarketQuery, ReferenceApplication, ReferenceKind, ReferenceQuery,
    ReferenceReadModel, ReferenceRecord, ReferenceRefreshResult,
};
