use std::future::Future;
use std::path::Path;

use sqlx::SqlitePool;

use super::provider_sync::{
    ProviderCandidateSelection, append_staged_page, clear_staged_pages, has_last_good, load_state,
    pending_coverage_state_changes, prepare_scan, provider_records, save_last_good,
    select_provider_candidate, set_option_underlying, set_source_desired_state, set_source_failure,
    source_definitions, staged_change_count, staged_pages, upsert_source_definition,
};
#[cfg(test)]
use super::provider_sync::{
    load_last_good, load_provider_candidate, option_underlyings, save_state, source_desired_states,
};
use super::sqlite::{open_pool, operation_lock, persistence};
use crate::domain::{
    ProviderCatalog, ReferenceResult, ReferenceSourceDefinition, SourceDesiredState,
};

#[derive(Clone)]
pub(crate) struct SqlxProviderSyncStore {
    pub(crate) pool: SqlitePool,
    normalized_promotion: bool,
}

impl SqlxProviderSyncStore {
    pub(crate) async fn source_scan_ids(
        &mut self,
        source_id: &str,
    ) -> ReferenceResult<Vec<kairos_primitives::reference::ReferenceSourceId>> {
        self.run(
            |pool| async move { super::provider_sync::source_scan_ids(&pool, source_id).await },
        )
        .await
    }

    pub(crate) async fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let pool = open_pool(path.as_ref()).await.map_err(persistence)?;
        Ok(Self {
            pool,
            normalized_promotion: true,
        })
    }

    pub(crate) fn from_pool(pool: SqlitePool) -> Self {
        Self {
            pool,
            normalized_promotion: true,
        }
    }

    #[cfg(test)]
    pub(crate) async fn open_legacy(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let pool = open_pool(path.as_ref()).await.map_err(persistence)?;
        Ok(Self {
            pool,
            normalized_promotion: false,
        })
    }

    pub(crate) fn supports_normalized_promotion(&self) -> bool {
        self.normalized_promotion
    }

    async fn run<T, F, Fut>(&self, operation: F) -> ReferenceResult<T>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: Future<Output = sqlx::Result<T>>,
    {
        let _guard = operation_lock().lock().await;
        operation(self.pool.clone()).await.map_err(persistence)
    }

    pub(crate) async fn prepare_scan(&mut self, provider: &str) -> ReferenceResult<bool> {
        if !self.normalized_promotion {
            return Ok(false);
        }
        let provider = provider.to_owned();
        self.run(|pool| async move { prepare_scan(&pool, &provider).await })
            .await
    }

    pub(crate) async fn load_state(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Option<(Option<String>, Option<ProviderCatalog>)>> {
        let provider = provider.to_owned();
        self.run(|pool| async move { load_state(&pool, &provider).await })
            .await
    }

    #[cfg(test)]
    pub(crate) async fn load_provider_candidate(
        &mut self,
        overlay: &ProviderCatalog,
        source_changes: &crate::services::sources::SourceChanges,
    ) -> ReferenceResult<ProviderCatalog> {
        self.run(
            |pool| async move { load_provider_candidate(&pool, overlay, source_changes).await },
        )
        .await
    }

    pub(crate) async fn select_provider_candidate(
        &mut self,
        overlay: &ProviderCatalog,
        source_changes: &crate::services::sources::SourceChanges,
    ) -> ReferenceResult<ProviderCandidateSelection> {
        self.run(
            |pool| async move { select_provider_candidate(&pool, overlay, source_changes).await },
        )
        .await
    }

    pub(crate) async fn pending_coverage_state_changes(
        &mut self,
        generation: kairos_primitives::time::Generation,
        first_event_sequence: kairos_primitives::time::Sequence,
        source_changes: &crate::services::sources::SourceChanges,
    ) -> ReferenceResult<
        Vec<(
            kairos_reference_contract::CoverageState,
            kairos_reference_contract::ReferenceCoverage,
        )>,
    > {
        self.run(|pool| async move {
            pending_coverage_state_changes(&pool, generation, first_event_sequence, source_changes)
                .await
        })
        .await
    }

    #[cfg(test)]
    pub(crate) async fn save_state(
        &mut self,
        provider: &str,
        cursor: Option<&str>,
        accumulated: Option<&ProviderCatalog>,
    ) -> ReferenceResult<()> {
        let records = accumulated
            .map(provider_records)
            .transpose()?
            .unwrap_or_default();
        let provider = provider.to_owned();
        let cursor = cursor.map(ToOwned::to_owned);
        self.run(|pool| async move { save_state(&pool, &provider, cursor, records).await })
            .await
    }

    #[cfg(test)]
    pub(crate) async fn load_last_good(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Option<ProviderCatalog>> {
        let provider = provider.to_owned();
        self.run(|pool| async move { load_last_good(&pool, &provider).await })
            .await
    }

    pub(crate) async fn has_last_good(&mut self, provider: &str) -> ReferenceResult<bool> {
        let provider = provider.to_owned();
        self.run(|pool| async move { has_last_good(&pool, &provider).await })
            .await
    }

    pub(crate) async fn save_last_good(
        &mut self,
        provider: &str,
        catalog: &ProviderCatalog,
    ) -> ReferenceResult<()> {
        let records = provider_records(catalog)?;
        let provider = provider.to_owned();
        let normalized = self.normalized_promotion;
        self.run(|pool| async move { save_last_good(&pool, &provider, records, normalized).await })
            .await
    }

    pub(crate) async fn append_staged_page(
        &mut self,
        provider: &str,
        cursor: Option<&str>,
        catalog: &ProviderCatalog,
    ) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        let cursor = cursor.map(ToOwned::to_owned);
        let records = provider_records(catalog)?;
        self.run(|pool| async move { append_staged_page(&pool, &provider, cursor, records).await })
            .await
    }

    pub(crate) async fn staged_pages(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Vec<ProviderCatalog>> {
        let provider = provider.to_owned();
        self.run(|pool| async move { staged_pages(&pool, &provider).await })
            .await
    }

    pub(crate) async fn clear_staged_pages(&mut self, provider: &str) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move { clear_staged_pages(&pool, &provider).await })
            .await
    }

    pub(crate) async fn staged_change_count(&mut self, provider: &str) -> ReferenceResult<u64> {
        let provider = provider.to_owned();
        self.run(|pool| async move { staged_change_count(&pool, &provider).await })
            .await
    }

    pub(crate) async fn source_definitions(
        &mut self,
    ) -> ReferenceResult<Vec<ReferenceSourceDefinition>> {
        self.run(|pool| async move { source_definitions(&pool).await })
            .await
    }

    #[cfg(test)]
    pub(crate) async fn source_desired_states(
        &mut self,
    ) -> ReferenceResult<Vec<(String, SourceDesiredState)>> {
        self.run(|pool| async move { source_desired_states(&pool).await })
            .await
            .map(|values| {
                values
                    .into_iter()
                    .map(|(source_id, desired_state)| {
                        (source_id, SourceDesiredState::from(desired_state.as_str()))
                    })
                    .collect()
            })
    }

    pub(crate) async fn upsert_source_definition(
        &mut self,
        definition: ReferenceSourceDefinition,
    ) -> ReferenceResult<()> {
        self.run(|pool| async move { upsert_source_definition(&pool, &definition).await })
            .await
    }

    pub(crate) async fn set_source_desired_state(
        &mut self,
        provider: &str,
        desired_state: SourceDesiredState,
    ) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            set_source_desired_state(&pool, &provider, desired_state.as_str()).await
        })
        .await
    }

    pub(crate) async fn set_source_failure(
        &mut self,
        source_id: &str,
        has_last_known_good: bool,
    ) -> ReferenceResult<()> {
        let source_id = source_id.to_owned();
        self.run(
            |pool| async move { set_source_failure(&pool, &source_id, has_last_known_good).await },
        )
        .await
    }

    #[cfg(test)]
    pub(crate) async fn option_underlyings(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Vec<String>> {
        let provider = provider.to_owned();
        self.run(|pool| async move { option_underlyings(&pool, &provider).await })
            .await
    }

    pub(crate) async fn set_option_underlying(
        &mut self,
        provider: &str,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        let underlying = underlying.to_owned();
        self.run(|pool| async move {
            set_option_underlying(&pool, &provider, &underlying, enabled).await
        })
        .await
    }
}
