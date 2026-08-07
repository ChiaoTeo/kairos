//! Ports owned by the reference application.

pub use crate::domain::{ProviderCatalog, ReferenceCatalog, ReferenceResult};

pub trait ReferenceSource: Send {
    fn source_id(&self) -> &str;
    fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog>;
}

pub trait CatalogStore: Send {
    fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>>;
    fn save(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<()>;
}

#[derive(Clone, Debug, Default)]
pub struct PublishedSnapshots {
    pub catalog: Vec<u8>,
    pub markets: Vec<u8>,
    pub lifecycle: Vec<u8>,
}
