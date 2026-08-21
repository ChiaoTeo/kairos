//! Generated process-boundary contracts and protocol metadata helpers.

#![allow(
    clippy::derivable_impls,
    clippy::extra_unused_lifetimes,
    clippy::missing_safety_doc,
    clippy::unnecessary_cast
)]

pub mod context;
pub mod contract;
pub mod control;
pub mod generated;
pub mod metadata;

pub use context::ProtocolContext;
pub use contract::{ContractClient, MissingContractEndpoint};

// Runtime identity values are owned by primitives.  Re-exporting them here is
// intentionally avoided: callers should make the primitives/protocol boundary
// visible in their imports.
