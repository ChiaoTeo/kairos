use crate::domain::{
    ProviderCatalog, ReferenceError, ReferenceResult, ReferenceSourceDefinition,
    SourceDesiredState, SourceScope, SourceTickBudget,
};
use crate::services::providers::{
    BinanceDerivativesSource, BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource,
    HyperliquidSource, MassiveEquitySource, MassiveOptionsCoverageSource, OkxSource,
    ProviderFanInSource, ReferenceCredentialResolver, activate_runtime_source_definition,
    deactivate_runtime_source_definition,
};
use crate::services::sources::{ParticipantAugmentedSource, ReferenceSource, SourceUpdate};
use crate::services::storage::provider_sync_store::SqlxProviderSyncStore;

type ProductionProviderFanIn = ProviderFanInSource<ConfiguredProviderSource>;

pub(crate) struct ConfiguredReferenceSource {
    inner: ParticipantAugmentedSource<ProductionProviderFanIn>,
}

pub(crate) enum ConfiguredProviderSource {
    BinanceSpot(BinanceSpotSource),
    BinanceDerivatives(BinanceDerivativesSource),
    BinanceOptions(BinanceOptionsSource),
    BinanceEquity(BinanceEquitySource),
    Okx(OkxSource),
    Hyperliquid(HyperliquidSource),
    MassiveEquity(MassiveEquitySource),
    MassiveOptions(MassiveOptionsCoverageSource),
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for ConfiguredProviderSource {
    fn source_id(&self) -> &str {
        match self {
            Self::BinanceSpot(source) => source.source_id(),
            Self::BinanceDerivatives(source) => source.source_id(),
            Self::BinanceOptions(source) => source.source_id(),
            Self::BinanceEquity(source) => source.source_id(),
            Self::Okx(source) => source.source_id(),
            Self::Hyperliquid(source) => source.source_id(),
            Self::MassiveEquity(source) => source.source_id(),
            Self::MassiveOptions(source) => source.source_id(),
        }
    }

    async fn activate_source_definition(
        definition: &ReferenceSourceDefinition,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        credentials: &ReferenceCredentialResolver,
        sync_store: Option<SqlxProviderSyncStore>,
    ) -> ReferenceResult<Option<Self>> {
        activate_runtime_source_definition(definition, connections, credentials, sync_store).await
    }

    fn deactivate_source_definition(
        definition: &ReferenceSourceDefinition,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<bool> {
        deactivate_runtime_source_definition(definition, connections)
    }

    async fn advance_workflow_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        match self {
            Self::BinanceSpot(source) => source.fetch_catalog_with_connections(connections).await,
            Self::BinanceDerivatives(source) => {
                source.fetch_catalog_with_connections(connections).await
            },
            Self::BinanceOptions(source) => {
                source.fetch_catalog_with_connections(connections).await
            },
            Self::BinanceEquity(source) => source.fetch_catalog_with_connections(connections).await,
            Self::Okx(source) => source.fetch_catalog_with_connections(connections).await,
            Self::Hyperliquid(source) => source.fetch_catalog_with_connections(connections).await,
            Self::MassiveEquity(source) => source.fetch_catalog_with_connections(connections).await,
            Self::MassiveOptions(source) => {
                source.fetch_catalog_with_connections(connections).await
            },
        }
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow_with_connections(connections).await
    }

    async fn advance_workflow_step(&mut self) -> ReferenceResult<SourceUpdate> {
        match self {
            Self::BinanceSpot(source) => source.fetch_catalog_step().await,
            Self::BinanceDerivatives(source) => source.fetch_catalog_step().await,
            Self::BinanceOptions(source) => source.fetch_catalog_step().await,
            Self::BinanceEquity(source) => source.fetch_catalog_step().await,
            Self::Okx(source) => source.fetch_catalog_step().await,
            Self::Hyperliquid(source) => source.fetch_catalog_step().await,
            Self::MassiveEquity(source) => source.fetch_catalog_step().await,
            Self::MassiveOptions(source) => source.fetch_catalog_step().await,
        }
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<SourceUpdate> {
        self.advance_workflow_step().await
    }

    async fn advance_workflow_step_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<SourceUpdate> {
        match self {
            Self::BinanceSpot(source) => {
                single_step(source.fetch_catalog_with_connections(connections).await?)
            },
            Self::BinanceDerivatives(source) => {
                single_step(source.fetch_catalog_with_connections(connections).await?)
            },
            Self::BinanceOptions(source) => {
                single_step(source.fetch_catalog_with_connections(connections).await?)
            },
            Self::BinanceEquity(source) => {
                single_step(source.fetch_catalog_with_connections(connections).await?)
            },
            Self::Okx(source) => {
                single_step(source.fetch_catalog_with_connections(connections).await?)
            },
            Self::Hyperliquid(source) => {
                single_step(source.fetch_catalog_with_connections(connections).await?)
            },
            Self::MassiveEquity(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            },
            Self::MassiveOptions(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            },
        }
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
        match self {
            Self::MassiveEquity(source) => {
                source
                    .fetch_catalog_step_with_budget(connections, budget)
                    .await
            },
            Self::MassiveOptions(source) => {
                source
                    .fetch_catalog_step_with_budget(connections, budget)
                    .await
            },
            _ => {
                self.advance_workflow_step_with_connections(connections)
                    .await
            },
        }
    }

    async fn fetch_catalog_step_with_budget(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<SourceUpdate> {
        self.advance_workflow_step_with_budget(connections, budget)
            .await
    }

    async fn set_source_scope_with_connections(
        &mut self,
        source_id: &str,
        scope: SourceScope,
        enabled: bool,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        if source_id != self.source_id() {
            return Err(ReferenceError::Invalid(format!(
                "{} cannot update scope for {source_id}",
                self.source_id()
            )));
        }
        match self {
            Self::MassiveOptions(source) => {
                source
                    .set_scope_with_connections(scope, enabled, connections)
                    .await
            },
            _ => Err(ReferenceError::Invalid(format!(
                "{} does not support scoped coverage",
                self.source_id()
            ))),
        }
    }

    fn option_underlyings(&self) -> Vec<String> {
        match self {
            Self::MassiveOptions(source) => source.option_underlyings(),
            _ => Vec::new(),
        }
    }
}

fn single_step(catalog: ProviderCatalog) -> ReferenceResult<SourceUpdate> {
    Ok(SourceUpdate::single(catalog))
}

#[cfg(not(test))]
impl ConfiguredProviderSource {
    pub(crate) fn massive_option_connection_plan(
        &self,
        underlying: &str,
    ) -> ReferenceResult<(
        kairos_conflux::ConnectionKey,
        kairos_conflux::MassiveRestConfig,
    )> {
        match self {
            Self::MassiveOptions(source) => source.connection_plan(underlying),
            _ => Err(ReferenceError::Invalid(format!(
                "{} does not support option coverage",
                self.source_id()
            ))),
        }
    }

    pub(crate) async fn set_managed_source_scope(
        &mut self,
        source_id: &str,
        scope: SourceScope,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
    ) -> ReferenceResult<()> {
        if source_id != self.source_id() {
            return Err(ReferenceError::Invalid(format!(
                "{} cannot update scope for {source_id}",
                self.source_id()
            )));
        }
        match self {
            Self::MassiveOptions(source) => {
                source
                    .set_scope_with_key(scope, enabled, connection_key)
                    .await
            },
            _ => Err(ReferenceError::Invalid(format!(
                "{} does not support scoped coverage",
                self.source_id()
            ))),
        }
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for ConfiguredReferenceSource {
    fn source_id(&self) -> &str {
        self.inner.source_id()
    }

    fn normalized_facts_authoritative(&self) -> bool {
        self.inner.normalized_facts_authoritative()
    }

    async fn advance_workflow(&mut self) -> ReferenceResult<ProviderCatalog> {
        self.inner.advance_workflow().await
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow().await
    }

    async fn advance_workflow_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        self.inner
            .advance_workflow_with_connections(connections)
            .await
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        self.advance_workflow_with_connections(connections).await
    }

    async fn advance_workflow_step(&mut self) -> ReferenceResult<SourceUpdate> {
        self.inner.advance_workflow_step().await
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<SourceUpdate> {
        self.advance_workflow_step().await
    }

    async fn advance_workflow_step_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<SourceUpdate> {
        self.inner
            .advance_workflow_step_with_connections(connections)
            .await
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
        self.inner
            .advance_workflow_step_with_budget(connections, budget)
            .await
    }

    async fn fetch_catalog_step_with_budget(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
        budget: SourceTickBudget,
    ) -> ReferenceResult<SourceUpdate> {
        self.advance_workflow_step_with_budget(connections, budget)
            .await
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

    fn source_health(&self) -> Vec<crate::domain::SourceHealth> {
        self.inner.source_health()
    }

    fn mark_promotions_committed(&mut self) {
        self.inner.mark_promotions_committed();
    }
}

impl ConfiguredReferenceSource {
    pub(crate) fn new(inner: ParticipantAugmentedSource<ProductionProviderFanIn>) -> Self {
        Self { inner }
    }

    #[cfg(not(test))]
    pub(crate) fn massive_option_connection_plan(
        &self,
        underlying: &str,
    ) -> ReferenceResult<(
        kairos_conflux::ConnectionKey,
        kairos_conflux::MassiveRestConfig,
    )> {
        self.inner.massive_option_connection_plan(underlying)
    }

    #[cfg(not(test))]
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
