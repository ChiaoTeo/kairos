//! Public reference use-case boundary.

use kairos_primitives::time::{Generation, Sequence};

use self::process::{ReferenceApplicationRuntime, ReferenceTickTiming};
use crate::domain::{ReferenceResult, SourceTickBudget};
use crate::services::actor::ReferenceActor;
use crate::services::providers::ReferenceSourcePlan;
use crate::services::storage::catalog_store::SqlxCatalogStore;

mod cli;
mod commands;
mod process;
mod queries;

#[cfg(test)]
mod tests;

kairos_reference_contract::reference_control_rpc_conflux_actor! {
    pub trait ReferenceRpcActor;
    service ReferenceRpcService;
}

pub use cli::{
    CliReferenceApplication, ConnectedReferenceApplication, ConnectedReferenceOutput,
    ReferenceCatalogCollection, ReferenceCatalogListRequest, ReferenceCatalogRecord,
    ReferenceCatalogStatusResult, ReferenceCliOutput, ReferenceMarketCatalogRequest,
    ReferenceOptionChainRequest, ReferenceProvidersResult,
};
pub use commands::{UpsertAssetCommand, UpsertInstrumentCommand, UpsertListingCommand};
pub(crate) use process::{
    ReferenceAppErrorSummary, ReferenceApplicationPhase, ReferenceTickTrigger,
};
pub use process::{ReferencePublication, ReferenceRefreshResult};
pub use queries::{
    MarketQuery, ReferenceKind, ReferenceQuery, ReferenceReadModel, ReferenceRecord,
};

/// Main-package use-case facade for Reference data.
///
/// Mutable catalog state remains owned by the private actor. Long-running
/// lifecycle, transport, status, and publication behavior is grouped under
/// the private `process` module instead of widening this facade.
pub struct ReferenceApplication {
    actor: ReferenceActor,
    refresh_interval: std::time::Duration,
    tick_budget: SourceTickBudget,
    initial_refresh: bool,
    runtime: ReferenceApplicationRuntime,
}

impl ReferenceApplication {
    pub(crate) async fn new(
        actor_id: impl Into<String>,
        workspace_id: impl Into<String>,
        source_plan: ReferenceSourcePlan,
        store: SqlxCatalogStore,
    ) -> ReferenceResult<Self> {
        let actor = ReferenceActor::new(actor_id, workspace_id, source_plan, store).await?;
        let runtime = ReferenceApplicationRuntime::new(actor.actor_id.as_str());
        Ok(Self {
            actor,
            refresh_interval: std::time::Duration::from_secs(300),
            tick_budget: SourceTickBudget::default(),
            initial_refresh: true,
            runtime,
        })
    }

    #[cfg(test)]
    pub(crate) async fn new_test<S>(
        actor_id: impl Into<String>,
        source: S,
        store: SqlxCatalogStore,
    ) -> ReferenceResult<Self>
    where
        S: crate::services::sources::ReferenceSource + 'static,
    {
        let actor = ReferenceActor::new_test(actor_id, source, store).await?;
        let runtime = ReferenceApplicationRuntime::new(actor.actor_id.as_str());
        Ok(Self {
            actor,
            refresh_interval: std::time::Duration::from_secs(300),
            tick_budget: SourceTickBudget::default(),
            initial_refresh: true,
            runtime,
        })
    }

    pub fn configure_conflux(
        &mut self,
        refresh_interval: std::time::Duration,
        initial_refresh: bool,
    ) {
        self.refresh_interval = refresh_interval;
        self.initial_refresh = initial_refresh;
    }

    pub(crate) fn configure_tick_budget(&mut self, tick_budget: SourceTickBudget) {
        self.tick_budget = tick_budget;
    }

    pub(crate) fn refresh_interval(&self) -> std::time::Duration {
        self.refresh_interval
    }

    pub(crate) fn tick_budget(&self) -> SourceTickBudget {
        self.tick_budget
    }

    pub(crate) fn initial_refresh(&self) -> bool {
        self.initial_refresh
    }

    pub(crate) fn app_phase(&self) -> ReferenceApplicationPhase {
        self.runtime.phase()
    }

    pub(crate) fn set_app_phase(&mut self, phase: ReferenceApplicationPhase) {
        self.runtime.set_phase(phase);
    }

    pub(crate) fn tick_timing(&self) -> ReferenceTickTiming {
        self.runtime.tick_timing()
    }

    pub(crate) fn last_tick_error(&self) -> Option<&ReferenceAppErrorSummary> {
        self.runtime.last_tick_error()
    }

    pub(crate) fn last_publication_error(&self) -> Option<&ReferenceAppErrorSummary> {
        self.runtime.last_publication_error()
    }

    pub(crate) fn record_publication_error_summary(
        &mut self,
        code: impl Into<String>,
        retryable: bool,
        message: impl Into<String>,
    ) {
        self.runtime
            .record_publication_error_summary(code, retryable, message);
    }

    pub(crate) fn record_publication_ready(&mut self) {
        self.runtime.record_publication_ready();
    }

    pub fn actor_id(&self) -> &kairos_primitives::runtime::ActorId {
        &self.actor.actor_id
    }

    pub fn generation(&self) -> Generation {
        self.actor.metadata.generation
    }

    pub fn event_sequence(&self) -> Sequence {
        self.actor.metadata.event_sequence
    }

    pub fn market_count(&self) -> usize {
        self.actor.metadata.market_count
    }
}
