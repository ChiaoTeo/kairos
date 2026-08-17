//! Internal source capability driven by the Reference actor.

use crate::domain::{ProviderCatalog, ProviderHealth, ReferenceError, ReferenceResult};

pub(crate) struct ProviderUpdate {
    pub catalog: ProviderCatalog,
    pub complete: bool,
    pub page_count: usize,
    pub facts_persisted: bool,
}

/// Internal seam over the Integration-owned catalog capabilities.
/// Concrete provider selection and mapping live in composition.
#[async_trait::async_trait]
pub(crate) trait ReferenceSource: Send {
    fn source_id(&self) -> &str;

    fn normalized_facts_authoritative(&self) -> bool {
        false
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog>;

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        Ok(ProviderUpdate {
            catalog: self.fetch_catalog().await?,
            complete: true,
            page_count: 1,
            facts_persisted: false,
        })
    }

    async fn advance_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support targeted refresh: {source_id}"
        )))
    }

    async fn set_source_paused(&mut self, source_id: &str, _paused: bool) -> ReferenceResult<()> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support runtime control: {source_id}"
        )))
    }

    async fn set_option_underlying(
        &mut self,
        underlying: &str,
        _enabled: bool,
    ) -> ReferenceResult<()> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support option coverage: {underlying}"
        )))
    }

    fn option_underlyings(&self) -> Vec<String> {
        Vec::new()
    }

    fn provider_health(&self) -> Vec<ProviderHealth> {
        Vec::new()
    }
}
