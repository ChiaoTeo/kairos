//! Reference business boundary.

pub mod application;
pub mod cli;
pub mod domain;
mod services;

pub use application::{
    MarketQuery, ReferenceApplication, ReferenceKind, ReferenceQuery, ReferenceReader,
    ReferenceRecord, ReferenceRefreshResult,
};
