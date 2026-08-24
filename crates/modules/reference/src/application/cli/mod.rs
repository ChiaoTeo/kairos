//! User-facing local and remote Reference CLI facades.

mod local;
mod remote;

pub use local::{
    CliReferenceApplication, ReferenceCatalogCollection, ReferenceCatalogListRequest,
    ReferenceCatalogRecord, ReferenceCatalogStatusResult, ReferenceCliOutput,
    ReferenceMarketCatalogRequest, ReferenceOptionChainRequest,
};
pub use remote::{
    ConnectedReferenceApplication, ConnectedReferenceOutput, ReferenceProvidersResult,
};

use super::{ReferenceKind, ReferenceQuery};
