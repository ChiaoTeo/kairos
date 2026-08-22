use crate::domain::{
    Entity, ProviderCatalog, ReferenceResult, ReferenceSourceDefinition, SourceDesiredState,
    SourceHealth, SourceScope, SourceTickBudget,
};
#[cfg(not(test))]
use crate::services::providers::ProviderFanInSource;
#[cfg(not(test))]
use crate::services::sources::ConfiguredProviderSource;
use crate::services::sources::{ReferenceSource, SourceUpdate};

pub(crate) struct ParticipantAugmentedSource<S> {
    inner: S,
    participants: Vec<Entity>,
}

impl<S> ParticipantAugmentedSource<S> {
    pub(crate) fn wrap(inner: S, participants: Vec<Entity>) -> Self {
        Self {
            inner,
            participants,
        }
    }
}

#[cfg(not(test))]
impl ParticipantAugmentedSource<ProviderFanInSource<ConfiguredProviderSource>> {
    pub(crate) fn massive_option_connection_plan(
        &self,
        underlying: &str,
    ) -> ReferenceResult<(
        kairos_conflux::ConnectionKey,
        kairos_conflux::MassiveRestConfig,
    )> {
        self.inner.massive_option_connection_plan(underlying)
    }

    pub(crate) async fn set_managed_source_scope(
        &mut self,
        source_id: &str,
        scope: SourceScope,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
    ) -> ReferenceResult<()> {
        self.inner
            .set_managed_source_scope(source_id, scope, enabled, connection_key)
            .await
    }
}

#[async_trait::async_trait(?Send)]
impl<S: ReferenceSource> ReferenceSource for ParticipantAugmentedSource<S> {
    fn source_id(&self) -> &str {
        self.inner.source_id()
    }

    fn normalized_facts_authoritative(&self) -> bool {
        self.inner.normalized_facts_authoritative()
    }

    async fn advance_workflow(&mut self) -> ReferenceResult<ProviderCatalog> {
        let mut catalog = self.inner.advance_workflow().await?;
        catalog.entities.extend(self.participants.iter().cloned());
        Ok(catalog)
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow().await
    }

    async fn advance_workflow_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        let mut catalog = self
            .inner
            .advance_workflow_with_connections(connections)
            .await?;
        catalog.entities.extend(self.participants.iter().cloned());
        Ok(catalog)
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow_with_connections(connections).await
    }

    async fn advance_workflow_step(&mut self) -> ReferenceResult<SourceUpdate> {
        let mut update = self.inner.advance_workflow_step().await?;
        update.note_appended_records_seen(self.participants.len());
        update
            .catalog
            .entities
            .extend(self.participants.iter().cloned());
        Ok(update)
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<SourceUpdate> {
        self.advance_workflow_step().await
    }

    async fn advance_workflow_step_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<SourceUpdate> {
        let mut update = self
            .inner
            .advance_workflow_step_with_connections(connections)
            .await?;
        update.note_appended_records_seen(self.participants.len());
        update
            .catalog
            .entities
            .extend(self.participants.iter().cloned());
        Ok(update)
    }

    async fn fetch_catalog_step_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<SourceUpdate> {
        self.advance_workflow_step_with_connections(connections)
            .await
    }

    async fn advance_workflow_step_with_budget(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<SourceUpdate> {
        let mut update = self
            .inner
            .advance_workflow_step_with_budget(connections, budget)
            .await?;
        update.note_appended_records_seen(self.participants.len());
        update
            .catalog
            .entities
            .extend(self.participants.iter().cloned());
        Ok(update)
    }

    async fn advance_one_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        self.inner.advance_one_source(source_id).await
    }

    async fn advance_source_with_connections(
        &mut self,
        source_id: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        self.inner
            .advance_source_with_connections(source_id, connections)
            .await
    }

    async fn advance_source_with_budget(
        &mut self,
        source_id: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        self.inner
            .advance_source_with_budget(source_id, connections, budget)
            .await
    }

    async fn set_source_desired_state(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
    ) -> ReferenceResult<()> {
        self.inner
            .set_source_desired_state(source_id, desired_state)
            .await
    }

    async fn set_source_desired_state_with_connections(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.inner
            .set_source_desired_state_with_connections(source_id, desired_state, connections)
            .await
    }

    async fn upsert_source_definition(
        &mut self,
        definition: ReferenceSourceDefinition,
    ) -> ReferenceResult<()> {
        self.inner.upsert_source_definition(definition).await
    }

    async fn upsert_source_definition_with_connections(
        &mut self,
        definition: ReferenceSourceDefinition,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.inner
            .upsert_source_definition_with_connections(definition, connections)
            .await
    }

    async fn set_source_scope_with_connections(
        &mut self,
        source_id: &str,
        scope: SourceScope,
        enabled: bool,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.inner
            .set_source_scope_with_connections(source_id, scope, enabled, connections)
            .await
    }

    fn option_underlyings(&self) -> Vec<String> {
        self.inner.option_underlyings()
    }

    fn source_health(&self) -> Vec<SourceHealth> {
        self.inner.source_health()
    }

    fn mark_promotions_committed(&mut self) {
        self.inner.mark_promotions_committed();
    }
}
