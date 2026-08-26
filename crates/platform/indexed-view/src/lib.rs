//! Concrete owner-scoped LMDB current-view storage.
//!
//! This crate owns storage mechanics and common metadata only. Business keys,
//! value schemas, retention, and semantic validation remain in module contract
//! crates.

mod metadata;
mod path;
mod store;

pub use metadata::{
    FORMAT_VERSION, IndexedViewIdentity, MetadataSnapshot, RebuildState, SchemaDescriptor,
    SchemaSet,
};
pub use path::environment_path;
pub use store::{
    EnvironmentOptions, IndexedViewReader, IndexedViewWriter, Mutation, PrefixRequest,
    ReadSnapshot, StoreError, ValueSnapshot,
};
