//! Async participant instrument catalog capability.

use std::future::Future;

use crate::IntegrationError;
use crate::domain::reference::{ExternalInstrumentCatalog, ExternalInstrumentCatalogPage};

pub trait InstrumentCatalogQuery: Send {
    fn fetch_instruments(
        &mut self,
    ) -> impl Future<Output = Result<ExternalInstrumentCatalog, IntegrationError>> + Send;

    fn fetch_instruments_page(
        &mut self,
        cursor: Option<&str>,
        limit: usize,
    ) -> impl Future<Output = Result<ExternalInstrumentCatalogPage, IntegrationError>> + Send;
}
