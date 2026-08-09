//! Reference-facing integration capability.

use crate::application::Connection;

pub use crate::domain::reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceEntity, ReferenceExecutionAccess,
    ReferenceFinancialProduct, ReferenceInstrument, ReferenceListing, ReferenceMarket,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceCatalogPage {
    pub catalog: ReferenceCatalogPayload,
    pub next_cursor: Option<String>,
    pub complete: bool,
}

pub trait ReferenceDataConnection: Connection {
    fn fetch_reference_catalog(&mut self) -> Result<ReferenceCatalogPayload, String>;

    fn fetch_reference_catalog_page(
        &mut self,
        _cursor: Option<&str>,
        _limit: usize,
    ) -> Result<ReferenceCatalogPage, String> {
        Ok(ReferenceCatalogPage {
            catalog: self.fetch_reference_catalog()?,
            next_cursor: None,
            complete: true,
        })
    }
}
