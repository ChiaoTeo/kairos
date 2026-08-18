//! Synchronous reference-data capabilities.

use crate::{ExternalInstrumentCatalog, ExternalInstrumentCatalogPage, IntegrationError};

pub trait InstrumentCatalogQuery: Send {
    fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError>;

    fn fetch_instruments_page(
        &mut self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<ExternalInstrumentCatalogPage, IntegrationError>;
}
