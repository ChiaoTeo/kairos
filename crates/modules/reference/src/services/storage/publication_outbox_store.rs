use std::future::Future;
#[cfg(test)]
use std::path::Path;

use sqlx::SqlitePool;

use super::publication_outbox::{
    acknowledge_publications, pending_event_count, pending_publications,
};
#[cfg(test)]
use super::sqlite::open_pool;
use super::sqlite::{operation_lock, persistence};
use crate::domain::ReferenceResult;
use crate::services::publication::EncodedPublication;

pub(crate) struct SqlxPublicationOutbox {
    pool: SqlitePool,
}

impl SqlxPublicationOutbox {
    pub(crate) fn from_pool(pool: SqlitePool) -> Self {
        Self { pool }
    }

    #[cfg(test)]
    pub(crate) async fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let pool = open_pool(path.as_ref()).await.map_err(persistence)?;
        Ok(Self { pool })
    }

    async fn run<T, F, Fut>(&self, operation: F) -> ReferenceResult<T>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: Future<Output = sqlx::Result<T>>,
    {
        let _guard = operation_lock().lock().await;
        operation(self.pool.clone()).await.map_err(persistence)
    }

    pub(crate) async fn pending_publications(
        &mut self,
        limit: usize,
    ) -> ReferenceResult<Vec<EncodedPublication>> {
        self.run(|pool| async move { pending_publications(&pool, limit).await })
            .await
    }

    pub(crate) async fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        self.run(|pool| async move { pending_event_count(&pool).await })
            .await
    }

    pub(crate) async fn acknowledge_publications(
        &mut self,
        event_ids: &[String],
    ) -> ReferenceResult<()> {
        let event_ids = event_ids.to_vec();
        self.run(|pool| async move { acknowledge_publications(&pool, &event_ids).await })
            .await
    }
}
