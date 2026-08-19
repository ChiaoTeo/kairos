//! Internal source capability driven by the Reference actor.

use crate::domain::{ProviderCatalog, ProviderHealth, ReferenceError, ReferenceResult};
use crate::services::providers::{
    BinanceDerivativesSource, BinanceEquitySource, BinanceOptionsSource, BinanceSpotSource,
    CompositeSource, HyperliquidSource, MassiveEquitySource, MassiveOptionsCoverageSource,
    OkxSource, ParticipantAugmentedSource,
};

pub(crate) struct ProviderUpdate {
    pub catalog: ProviderCatalog,
    pub complete: bool,
    pub page_count: usize,
    pub facts_persisted: bool,
}

/// Internal seam over the Integration-owned catalog capabilities.
/// Concrete provider selection and mapping live in composition.
#[async_trait::async_trait(?Send)]
pub(crate) trait ReferenceSource: Send {
    fn source_id(&self) -> &str;

    fn normalized_facts_authoritative(&self) -> bool {
        false
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

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        Ok(ProviderUpdate {
            catalog: self.fetch_catalog().await?,
            complete: true,
            page_count: 1,
            facts_persisted: false,
        })
    }

    async fn fetch_catalog_step_with_connections(
        &mut self,
        _connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderUpdate> {
        self.fetch_catalog_step().await
    }

    async fn advance_source(
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
        self.advance_source(source_id).await
    }

    async fn set_source_paused(&mut self, source_id: &str, _paused: bool) -> ReferenceResult<()> {
        Err(ReferenceError::Invalid(format!(
            "reference source does not support runtime control: {source_id}"
        )))
    }

    #[cfg(test)]
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

type ProductionComposite = CompositeSource<ConfiguredProviderSource>;

pub(crate) struct ConfiguredReferenceSource {
    inner: ParticipantAugmentedSource<ProductionComposite>,
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

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<crate::domain::ProviderCatalog> {
        match self {
            Self::BinanceSpot(source) => source.fetch_catalog_with_connections(connections).await,
            Self::BinanceDerivatives(source) => {
                source.fetch_catalog_with_connections(connections).await
            }
            Self::BinanceOptions(source) => {
                source.fetch_catalog_with_connections(connections).await
            }
            Self::BinanceEquity(source) => source.fetch_catalog_with_connections(connections).await,
            Self::Okx(source) => source.fetch_catalog_with_connections(connections).await,
            Self::Hyperliquid(source) => source.fetch_catalog_with_connections(connections).await,
            Self::MassiveEquity(source) => source.fetch_catalog_with_connections(connections).await,
            Self::MassiveOptions(source) => {
                source.fetch_catalog_with_connections(connections).await
            }
        }
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
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

    async fn fetch_catalog_step_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderUpdate> {
        match self {
            Self::BinanceSpot(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            }
            Self::BinanceDerivatives(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            }
            Self::BinanceOptions(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            }
            Self::BinanceEquity(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            }
            Self::Okx(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            }
            Self::Hyperliquid(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            }
            Self::MassiveEquity(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            }
            Self::MassiveOptions(source) => {
                source
                    .fetch_catalog_step_with_connections(connections)
                    .await
            }
        }
    }

    #[cfg(test)]
    async fn set_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<()> {
        match self {
            Self::MassiveOptions(source) => source.set_option_underlying(underlying, enabled).await,
            _ => Err(crate::domain::ReferenceError::Invalid(format!(
                "{} does not support option coverage",
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

    pub(crate) async fn set_managed_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
    ) -> ReferenceResult<()> {
        match self {
            Self::MassiveOptions(source) => {
                source
                    .set_option_underlying_with_key(underlying, enabled, connection_key)
                    .await
            }
            _ => Err(ReferenceError::Invalid(format!(
                "{} does not support option coverage",
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

    async fn fetch_catalog(&mut self) -> ReferenceResult<crate::domain::ProviderCatalog> {
        self.inner.fetch_catalog().await
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<crate::domain::ProviderCatalog> {
        self.inner.fetch_catalog_with_connections(connections).await
    }

    async fn fetch_catalog_step(&mut self) -> ReferenceResult<ProviderUpdate> {
        self.inner.fetch_catalog_step().await
    }

    async fn fetch_catalog_step_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderUpdate> {
        self.inner
            .fetch_catalog_step_with_connections(connections)
            .await
    }

    async fn advance_source(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Option<crate::domain::ProviderCatalog>> {
        self.inner.advance_source(source_id).await
    }

    async fn advance_source_with_connections(
        &mut self,
        source_id: &str,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<Option<crate::domain::ProviderCatalog>> {
        self.inner
            .advance_source_with_connections(source_id, connections)
            .await
    }

    async fn set_source_paused(&mut self, source_id: &str, paused: bool) -> ReferenceResult<()> {
        self.inner.set_source_paused(source_id, paused).await
    }

    #[cfg(test)]
    async fn set_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<()> {
        self.inner.set_option_underlying(underlying, enabled).await
    }

    fn option_underlyings(&self) -> Vec<String> {
        self.inner.option_underlyings()
    }

    fn provider_health(&self) -> Vec<crate::domain::ProviderHealth> {
        self.inner.provider_health()
    }
}

impl ConfiguredReferenceSource {
    pub(crate) fn new(inner: ParticipantAugmentedSource<ProductionComposite>) -> Self {
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
    pub(crate) async fn set_managed_option_underlying(
        &mut self,
        underlying: &str,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
    ) -> ReferenceResult<()> {
        self.inner
            .set_managed_option_underlying(underlying, enabled, connection_key)
            .await
    }
}
