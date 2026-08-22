//! Public reference use-case facade.
//!
//! The facade owns the reference actor but does not expose it.  Callers use
//! refresh/publish commands or read-only market queries; persistence,
//! provider connections and publication transports stay behind private services.

use kairos_primitives::time::{Generation, Sequence};

use crate::application::runtime::{
    ReferenceAppErrorSummary, ReferenceApplicationPhase, ReferenceApplicationRuntime,
    ReferenceTickTiming,
};
use crate::domain::{ReferenceResult, SourceTickBudget};
use crate::services::actor::ReferenceActor;
use crate::services::providers::ReferenceSourcePlan;
use crate::services::storage::catalog_store::SqlxCatalogStore;

/// Public application boundary for reference data.
pub struct ReferenceApplication {
    pub(super) actor: ReferenceActor,
    pub(super) refresh_interval: std::time::Duration,
    pub(super) tick_budget: SourceTickBudget,
    initial_refresh: bool,
    pub(super) runtime: ReferenceApplicationRuntime,
}

impl ReferenceApplication {
    pub(crate) async fn new(
        actor_id: impl Into<String>,
        source_plan: ReferenceSourcePlan,
        store: SqlxCatalogStore,
    ) -> ReferenceResult<Self> {
        let actor = ReferenceActor::new(actor_id, source_plan, store).await?;
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

    pub fn actor_id(&self) -> &str {
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
