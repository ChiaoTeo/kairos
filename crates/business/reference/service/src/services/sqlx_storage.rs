//! SQLx-backed Reference persistence.
//!
//! The application traits are synchronous for now. Each Store owns one
//! long-lived current-thread Tokio runtime, so synchronous calls do not create
//! a fresh runtime per database operation.

use std::future::Future;
use std::path::Path;

use sqlx::{
    sqlite::{SqliteConnectOptions, SqlitePoolOptions},
    Row, SqlitePool,
};

use super::store::{CatalogStore, ProviderSyncStore};
use crate::domain::{
    LifecycleEvent, ProviderCatalog, ReferenceCatalog, ReferenceError, ReferenceResult,
};

const LIFECYCLE_LIMIT: i64 = 4096;

pub(crate) struct SqlxCatalogStore {
    pool: SqlitePool,
    runtime: tokio::runtime::Runtime,
}

pub(crate) struct SqlxProviderSyncStore {
    pool: SqlitePool,
    runtime: tokio::runtime::Runtime,
}

fn persistence(error: impl std::fmt::Display) -> ReferenceError {
    ReferenceError::Persistence(error.to_string())
}

fn run_with_runtime<T, F, Fut>(
    runtime: &tokio::runtime::Runtime,
    pool: &SqlitePool,
    operation: F,
) -> ReferenceResult<T>
where
    F: FnOnce(SqlitePool) -> Fut,
    Fut: Future<Output = sqlx::Result<T>>,
{
    runtime
        .block_on(operation(pool.clone()))
        .map_err(persistence)
}

fn decode<T: serde::de::DeserializeOwned>(payload: String) -> ReferenceResult<T> {
    serde_json::from_str(&payload).map_err(persistence)
}

fn runtime() -> ReferenceResult<tokio::runtime::Runtime> {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(persistence)
}

async fn open_pool(path: &Path) -> sqlx::Result<SqlitePool> {
    let options = SqliteConnectOptions::new()
        .filename(path)
        .create_if_missing(true);
    let pool = SqlitePoolOptions::new()
        .max_connections(4)
        .connect_with(options)
        .await?;
    sqlx::query("PRAGMA journal_mode = WAL")
        .execute(&pool)
        .await?;
    sqlx::query("PRAGMA synchronous = NORMAL")
        .execute(&pool)
        .await?;
    sqlx::query("PRAGMA busy_timeout = 5000")
        .execute(&pool)
        .await?;
    sqlx::migrate!("./migrations").run(&pool).await?;
    Ok(pool)
}

impl SqlxCatalogStore {
    pub(crate) fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let path = path.as_ref().to_path_buf();
        let runtime = runtime()?;
        let pool = runtime.block_on(open_pool(&path)).map_err(persistence)?;
        Ok(Self { pool, runtime })
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::{CatalogStore, ProviderSyncStore, SqlxCatalogStore, SqlxProviderSyncStore};
    use crate::domain::{LifecycleEvent, ProviderCatalog, ReferenceCatalog};

    #[test]
    fn sqlx_catalog_round_trips_state_and_outbox() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let mut store = SqlxCatalogStore::open(&path).unwrap();
        let events = vec![LifecycleEvent {
            event_id: "reference:00000000000000000001".into(),
            event_type: "listed".into(),
            ..Default::default()
        }];
        store
            .save_refresh(&ReferenceCatalog::default(), &events)
            .unwrap();
        assert_eq!(store.pending_event_count().unwrap(), 1);
        assert_eq!(store.pending_events(10).unwrap(), events);
        assert!(store.load().unwrap().is_some());
    }

    #[test]
    fn sqlx_provider_state_survives_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let catalog = ProviderCatalog::default();
        {
            let mut store = SqlxProviderSyncStore::open(&path).unwrap();
            store
                .save_state("massive", Some("cursor-1"), Some(&catalog))
                .unwrap();
        }
        let mut reopened = SqlxProviderSyncStore::open(&path).unwrap();
        let (cursor, value) = reopened.load_state("massive").unwrap().unwrap();
        assert_eq!(cursor.as_deref(), Some("cursor-1"));
        assert_eq!(value.unwrap(), catalog);
    }

    #[test]
    fn reference_catalog_golden_fixture_matches_rust_domain_contract() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../../tests/fixtures/reference_catalog_empty.json");
        let payload = std::fs::read_to_string(path).unwrap();
        let catalog: ReferenceCatalog = serde_json::from_str(&payload).unwrap();
        assert_eq!(catalog, ReferenceCatalog::default());
    }
}

impl SqlxProviderSyncStore {
    pub(crate) fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let path = path.as_ref().to_path_buf();
        let runtime = runtime()?;
        let pool = runtime.block_on(open_pool(&path)).map_err(persistence)?;
        Ok(Self { pool, runtime })
    }
}

impl SqlxCatalogStore {
    fn run<T, F, Fut>(&self, operation: F) -> ReferenceResult<T>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: Future<Output = sqlx::Result<T>>,
    {
        run_with_runtime(&self.runtime, &self.pool, operation)
    }
}

impl SqlxProviderSyncStore {
    fn run<T, F, Fut>(&self, operation: F) -> ReferenceResult<T>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: Future<Output = sqlx::Result<T>>,
    {
        run_with_runtime(&self.runtime, &self.pool, operation)
    }
}

impl ProviderSyncStore for SqlxProviderSyncStore {
    fn load_state(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Option<(Option<String>, Option<ProviderCatalog>)>> {
        self.run(|pool| async move {
            let row = sqlx::query("SELECT cursor, accumulated_catalog FROM reference_provider_sync WHERE provider = ?")
                .bind(provider).fetch_optional(&pool).await?;
            row.map(|row| {
                let cursor = row.try_get::<Option<String>, _>("cursor")?;
                let payload = row.try_get::<Option<String>, _>("accumulated_catalog")?;
                let catalog = payload
                    .map(decode)
                    .transpose()
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                Ok((cursor, catalog))
            })
            .transpose()
            .map_err(|error: sqlx::Error| error)
        })
    }

    fn save_state(
        &mut self,
        provider: &str,
        cursor: Option<&str>,
        accumulated: Option<&ProviderCatalog>,
    ) -> ReferenceResult<()> {
        let payload = accumulated
            .map(serde_json::to_string)
            .transpose()
            .map_err(persistence)?;
        self.run(|pool| async move {
            sqlx::query("INSERT INTO reference_provider_sync(provider, cursor, accumulated_catalog, updated_at_unix_nanos) VALUES (?, ?, ?, ?) ON CONFLICT(provider) DO UPDATE SET cursor = excluded.cursor, accumulated_catalog = excluded.accumulated_catalog, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(provider).bind(cursor).bind(payload).bind(crate::domain::unix_nanos().get() as i64)
                .execute(&pool).await?;
            Ok(())
        })
    }

    fn load_last_good(&mut self, provider: &str) -> ReferenceResult<Option<ProviderCatalog>> {
        self.run(|pool| async move {
            let payload = sqlx::query_scalar::<_, Option<String>>(
                "SELECT last_good_catalog FROM reference_provider_sync WHERE provider = ?",
            )
            .bind(provider)
            .fetch_optional(&pool)
            .await?
            .flatten();
            payload
                .map(decode)
                .transpose()
                .map_err(|error| sqlx::Error::Protocol(error.to_string()))
        })
    }

    fn save_last_good(&mut self, provider: &str, catalog: &ProviderCatalog) -> ReferenceResult<()> {
        let payload = serde_json::to_string(catalog).map_err(persistence)?;
        self.run(|pool| async move {
            sqlx::query("INSERT INTO reference_provider_sync(provider, last_good_catalog, updated_at_unix_nanos) VALUES (?, ?, ?) ON CONFLICT(provider) DO UPDATE SET last_good_catalog = excluded.last_good_catalog, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(provider).bind(payload).bind(crate::domain::unix_nanos().get() as i64)
                .execute(&pool).await?;
            Ok(())
        })
    }
}

impl CatalogStore for SqlxCatalogStore {
    fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
        self.run(|pool| async move {
            let Some(payload) = sqlx::query_scalar::<_, String>(
                "SELECT payload FROM reference_catalog WHERE id = 1",
            )
            .fetch_optional(&pool)
            .await?
            else {
                return Ok(None);
            };
            let mut catalog: ReferenceCatalog =
                decode(payload).map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
            let rows = sqlx::query(
                "SELECT payload FROM reference_lifecycle ORDER BY sequence DESC LIMIT ?",
            )
            .bind(LIFECYCLE_LIMIT)
            .fetch_all(&pool)
            .await?;
            catalog.lifecycle_events = rows
                .into_iter()
                .map(|row| {
                    decode(row.try_get("payload")?)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))
                })
                .collect::<Result<_, _>>()?;
            catalog.lifecycle_events.reverse();
            Ok(Some(catalog))
        })
    }

    fn save(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<()> {
        self.save_refresh(catalog, &[])
    }

    fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
    ) -> ReferenceResult<()> {
        let mut state = catalog.clone();
        state.lifecycle_events.clear();
        let state_payload = serde_json::to_string(&state).map_err(persistence)?;
        let event_payloads = events
            .iter()
            .map(|event| serde_json::to_string(event).map(|payload| (event, payload)))
            .collect::<Result<Vec<_>, _>>()
            .map_err(persistence)?;
        let event_count = events.len() as u64;
        let result = self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            sqlx::query("INSERT INTO reference_catalog(id, payload) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload = excluded.payload").bind(state_payload).execute(&mut *tx).await?;
            for (offset, (event, payload)) in event_payloads.into_iter().enumerate() {
                let market_id = event.market_id.as_ref().map(ToString::to_string);
                let exchange_id = event.exchange_id.as_ref().map(ToString::to_string);
                let sequence = catalog
                    .event_sequence
                    .get()
                    .saturating_sub(events.len() as u64)
                    .saturating_add(offset as u64 + 1) as i64;
                sqlx::query("INSERT OR IGNORE INTO reference_lifecycle(sequence,event_type,record_kind,record_id,market_id,exchange_id,event_time_unix_nanos,payload) VALUES (?,?,?,?,?,?,?,?)")
                    .bind(sequence).bind(&event.event_type).bind(&event.record_kind).bind(&event.record_id).bind(market_id).bind(exchange_id).bind(event.event_time_unix_nanos.get() as i64).bind(&payload).execute(&mut *tx).await?;
                sqlx::query("INSERT OR IGNORE INTO reference_pending_publication(event_id,payload) VALUES (?,?)").bind(&event.event_id).bind(payload).execute(&mut *tx).await?;
            }
            tx.commit().await
        });
        if result.is_ok() {
            kairos_workspace::logging::record_counter("kairos.reference.refresh.commit", 1);
            if event_count > 0 {
                kairos_workspace::logging::record_counter(
                    "kairos.reference.lifecycle.append",
                    event_count,
                );
            }
        }
        result
    }

    fn pending_events(&mut self, limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.payloads(
            "SELECT payload FROM reference_pending_publication ORDER BY rowid LIMIT ?",
            limit as i64,
        )
    }
    fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        self.run(|pool| async move {
            Ok(
                sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM reference_pending_publication")
                    .fetch_one(&pool)
                    .await? as usize,
            )
        })
    }
    fn lifecycle_events(
        &mut self,
        from: Option<u64>,
        to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.lifecycle_payloads(from, to, None, None, limit)
    }
    fn lifecycle_events_filtered(
        &mut self,
        from: Option<u64>,
        to: Option<u64>,
        time_from: Option<u64>,
        time_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.lifecycle_payloads(from, to, time_from, time_to, limit)
    }
    fn acknowledge_pending_events(&mut self, event_ids: &[String]) -> ReferenceResult<()> {
        let ids = event_ids.to_vec();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            for id in ids {
                sqlx::query("DELETE FROM reference_pending_publication WHERE event_id = ?")
                    .bind(id)
                    .execute(&mut *tx)
                    .await?;
            }
            tx.commit().await
        })
    }
}

impl SqlxCatalogStore {
    fn payloads(&self, query: &'static str, limit: i64) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.run(|pool| async move {
            let rows = sqlx::query(query).bind(limit).fetch_all(&pool).await?;
            rows.into_iter()
                .map(|row| {
                    decode(row.try_get("payload")?)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))
                })
                .collect()
        })
    }
    fn lifecycle_payloads(
        &self,
        from: Option<u64>,
        to: Option<u64>,
        time_from: Option<u64>,
        time_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.run(|pool| async move {
            let rows = sqlx::query("SELECT payload FROM reference_lifecycle WHERE sequence >= COALESCE(?, 1) AND sequence <= COALESCE(?, 9223372036854775807) AND (? IS NULL OR event_time_unix_nanos >= ?) AND (? IS NULL OR event_time_unix_nanos < ?) ORDER BY sequence LIMIT ?").bind(from.map(|v| v as i64)).bind(to.map(|v| v as i64)).bind(time_from.map(|v| v as i64)).bind(time_from.map(|v| v as i64)).bind(time_to.map(|v| v as i64)).bind(time_to.map(|v| v as i64)).bind(limit as i64).fetch_all(&pool).await?;
            rows.into_iter()
                .map(|row| {
                    decode(row.try_get("payload")?)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))
                })
                .collect()
        })
    }
}
