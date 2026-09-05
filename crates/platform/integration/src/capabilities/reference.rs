//! Async participant instrument catalog capability.

use std::future::Future;

use crate::IntegrationError;
use crate::domain::reference::{ExternalInstrumentCatalog, ExternalInstrumentCatalogPage};

/// Catalog reads do not own scan cursors; callers supply the continuation.
pub trait InstrumentCatalogQuery: Send + Sync {
    fn fetch_instruments(
        &self,
    ) -> impl Future<Output = Result<ExternalInstrumentCatalog, IntegrationError>> + Send;

    fn fetch_instruments_page(
        &self,
        cursor: Option<&str>,
        limit: usize,
    ) -> impl Future<Output = Result<ExternalInstrumentCatalogPage, IntegrationError>> + Send;
}
