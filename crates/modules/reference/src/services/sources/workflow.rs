use crate::domain::{
    ProviderCatalog, ReferenceError, ReferenceResult, ReferenceSourceDefinition,
    SourceDesiredState, SourceHealth, SourceTickBudget,
};
use crate::services::providers::{ReferenceCredentialResolver, ReferenceSourceBinding};
use crate::services::sources::SourceUpdate;
use crate::services::storage::provider_sync_store::SqlxProviderSyncStore;

/// Internal source workflow capability driven by the Reference actor.
///
/// Provider implementations may still fetch a catalog page or product snapshot
/// internally, but the actor enters them through workflow advance methods so
/// the runtime model is not expressed as a one-shot catalog refresh.
#[async_trait::async_trait(?Send)]
pub(crate) trait ReferenceSource: Send {
    fn source_id(&self) -> &str;

    async fn activate_source_definition(
        _definition: &ReferenceSourceDefinition,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
        _credentials: &ReferenceCredentialResolver,
        _sync_store: Option<SqlxProviderSyncStore>,
    ) -> ReferenceResult<Option<Self>>
    where
        Self: Sized,
    {
        Ok(None)
    }

    fn deactivate_source_definition(
        _definition: &ReferenceSourceDefinition,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<bool>
    where
        Self: Sized,
    {
        Ok(false)
    }

    fn source_definition(&self) -> ReferenceResult<ReferenceSourceDefinition> {
        match ReferenceSourceBinding::from_source_id(self.source_id()) {
            Some(binding) => binding.builtin_definition(),
            None => ReferenceSourceDefinition::runtime_default(self.source_id()),
        }
    }

    fn normalized_facts_authoritative(&self) -> bool {
        false
    }

    async fn advance_workflow(&mut self) -> ReferenceResult<ProviderCatalog> {
        self.fetch_catalog().await
    }

    async fn advance_workflow_with_connections(
        &mut self,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow().await
    }

    async fn advance_workflow_with_budget(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        _budget: SourceTickBudget,
    ) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow_with_connections(connections).await
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        Err(ReferenceError::Invalid(format!(
            "reference source `{}` requires Conflux-managed connections",
            self.source_id()
        )))
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        self.fetch_catalog().await
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<SourceUpdate> {
        Ok(SourceUpdate::single(self.fetch_catalog().await?))
    }

    async fn fetch_catalog_step_with_connections(
        &mut self,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<SourceUpdate> {
        self.fetch_catalog_step().await
    }

    async fn fetch_catalog_step_with_budget(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        _budget: SourceTickBudget,
    ) -> ReferenceResult<SourceUpdate> {
        self.fetch_catalog_step_with_connections(connections).await
    }

    async fn advance_workflow_step(&mut self) -> ReferenceResult<SourceUpdate> {
        self.fetch_catalog_step().await
    }

    async fn advance_workflow_step_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<SourceUpdate> {
        self.fetch_catalog_step_with_connections(connections).await
    }

    async fn advance_workflow_step_with_budget(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<SourceUpdate> {
        self.fetch_catalog_step_with_budget(connections, budget)
            .await
    }

    async fn advance_one_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support targeted refresh: {source_id}"
        )))
    }

    async fn advance_source_with_connections(
        &mut self,
        source_id: &str,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        self.advance_one_source(source_id).await
    }

    async fn advance_source_with_budget(
        &mut self,
        source_id: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        _budget: SourceTickBudget,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        self.advance_source_with_connections(source_id, connections)
            .await
    }

    #[cfg(test)]
    async fn advance_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        self.advance_one_source(source_id).await
    }

    async fn set_source_desired_state(
        &mut self,
        source_id: &str,
        _desired_state: SourceDesiredState,
    ) -> ReferenceResult<()> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support runtime control: {source_id}"
        )))
    }

    async fn set_source_desired_state_with_connections(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.set_source_desired_state(source_id, desired_state)
            .await
    }

    async fn upsert_source_definition(
        &mut self,
        definition: ReferenceSourceDefinition,
    ) -> ReferenceResult<()> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support dynamic source registration: {}",
            definition.source_id
        )))
    }

    async fn upsert_source_definition_with_connections(
        &mut self,
        definition: ReferenceSourceDefinition,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.upsert_source_definition(definition).await
    }

    async fn set_source_scope_with_connections(
        &mut self,
        source_id: &str,
        _scope: crate::domain::SourceScope,
        _enabled: bool,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support scoped coverage: {source_id}"
        )))
    }

    fn option_underlyings(&self) -> Vec<String> {
        Vec::new()
    }

    fn source_health(&self) -> Vec<SourceHealth> {
        Vec::new()
    }

    fn mark_promotions_committed(&mut self) {}
}
