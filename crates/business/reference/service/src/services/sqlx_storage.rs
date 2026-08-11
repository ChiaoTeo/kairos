//! SQLx-backed Reference persistence running on the caller's Tokio runtime.

use async_trait::async_trait;
use std::future::Future;
use std::path::Path;

use sqlx::{
    sqlite::{SqliteConnectOptions, SqlitePoolOptions},
    QueryBuilder, Row, Sqlite, SqlitePool,
};

use super::store::{CatalogStore, ProviderSyncStore};
use crate::domain::{
    LifecycleEvent, ProviderCatalog, ReferenceCatalog, ReferenceError, ReferenceResult,
};

const LIFECYCLE_LIMIT: i64 = 4096;

pub(crate) struct SqlxCatalogStore {
    pool: SqlitePool,
}

pub(crate) struct SqlxProviderSyncStore {
    pool: SqlitePool,
}

fn persistence(error: impl std::fmt::Display) -> ReferenceError {
    ReferenceError::Persistence(error.to_string())
}

fn decode<T: serde::de::DeserializeOwned>(payload: String) -> ReferenceResult<T> {
    serde_json::from_str(&payload).map_err(persistence)
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
    pub(crate) async fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let pool = open_pool(path.as_ref()).await.map_err(persistence)?;
        Ok(Self { pool })
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::{CatalogStore, ProviderSyncStore, SqlxCatalogStore, SqlxProviderSyncStore};
    use crate::domain::{LifecycleEvent, ProviderCatalog, ReferenceCatalog};

    #[tokio::test]
    async fn sqlx_catalog_round_trips_state_and_outbox() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let events = vec![LifecycleEvent {
            event_id: "reference:00000000000000000001".into(),
            event_type: "listed".into(),
            ..Default::default()
        }];
        let catalog = ReferenceCatalog {
            lifecycle_events: events.clone(),
            generation: 1.into(),
            event_sequence: 1.into(),
            ..ReferenceCatalog::default()
        };
        {
            let mut store = SqlxCatalogStore::open(&path).await.unwrap();
            store.save_refresh(&catalog, &events).await.unwrap();
        }

        let mut reopened = SqlxCatalogStore::open(&path).await.unwrap();
        assert_eq!(reopened.load().await.unwrap(), Some(catalog.clone()));
        assert_eq!(reopened.pending_event_count().await.unwrap(), 1);
        // Idempotent refresh persistence must not inflate the materialized
        // counter when the event ID already exists in the outbox.
        reopened.save_refresh(&catalog, &events).await.unwrap();
        assert_eq!(reopened.pending_event_count().await.unwrap(), 1);
        assert_eq!(reopened.pending_events(10).await.unwrap(), events);
        reopened
            .acknowledge_pending_events(&["reference:unknown".into()])
            .await
            .unwrap();
        assert_eq!(reopened.pending_event_count().await.unwrap(), 1);
        reopened
            .acknowledge_pending_events(&["reference:00000000000000000001".into()])
            .await
            .unwrap();
        assert_eq!(reopened.pending_event_count().await.unwrap(), 0);
    }

    #[tokio::test]
    async fn sqlx_provider_state_survives_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let catalog = ProviderCatalog::default();
        {
            let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
            store
                .save_state("massive", Some("cursor-1"), Some(&catalog))
                .await
                .unwrap();
        }
        let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
        let (cursor, value) = reopened.load_state("massive").await.unwrap().unwrap();
        assert_eq!(cursor.as_deref(), Some("cursor-1"));
        assert_eq!(value.unwrap(), catalog);
    }

    #[tokio::test]
    async fn provider_staging_advances_cursor_without_a_growing_accumulated_payload() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let first = ProviderCatalog {
            markets: vec![crate::domain::Market {
                market_id: kairos_domain_types::MarketId::new("market:first").unwrap(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let second = ProviderCatalog {
            markets: vec![crate::domain::Market {
                market_id: kairos_domain_types::MarketId::new("market:second").unwrap(),
                status: "active".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
        store
            .append_staged_page("massive-options", Some("cursor-1"), &first)
            .await
            .unwrap();
        store
            .append_staged_page("massive-options", Some("cursor-2"), &second)
            .await
            .unwrap();

        let (cursor, accumulated) = store.load_state("massive-options").await.unwrap().unwrap();
        assert_eq!(cursor.as_deref(), Some("cursor-2"));
        assert!(accumulated.is_none());
        let pages = store.staged_pages("massive-options").await.unwrap();
        assert_eq!(pages.len(), 2);
        assert_eq!(pages[0], first);
        assert_eq!(pages[1], second);

        store.clear_staged_pages("massive-options").await.unwrap();
        assert!(store
            .staged_pages("massive-options")
            .await
            .unwrap()
            .is_empty());
        let (cursor, accumulated) = store.load_state("massive-options").await.unwrap().unwrap();
        assert!(cursor.is_none());
        assert!(accumulated.is_none());
    }

    #[tokio::test]
    async fn provider_pause_control_survives_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        {
            let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
            store
                .set_source_paused("massive-options", true)
                .await
                .unwrap();
        }

        let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
        assert_eq!(
            reopened.paused_sources().await.unwrap(),
            vec!["massive-options"]
        );
        reopened
            .set_source_paused("massive-options", false)
            .await
            .unwrap();
        assert!(reopened.paused_sources().await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn option_coverage_survives_reopen_and_normalizes_by_key() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        {
            let mut store = SqlxProviderSyncStore::open(&path).await.unwrap();
            store
                .set_option_underlying("massive-options", "SPY", true)
                .await
                .unwrap();
            store
                .set_option_underlying("massive-options", "AAPL", true)
                .await
                .unwrap();
        }
        let mut reopened = SqlxProviderSyncStore::open(&path).await.unwrap();
        assert_eq!(
            reopened
                .option_underlyings("massive-options")
                .await
                .unwrap(),
            vec!["AAPL", "SPY"]
        );
        reopened
            .set_option_underlying("massive-options", "SPY", false)
            .await
            .unwrap();
        assert_eq!(
            reopened
                .option_underlyings("massive-options")
                .await
                .unwrap(),
            vec!["AAPL"]
        );
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
    pub(crate) async fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let pool = open_pool(path.as_ref()).await.map_err(persistence)?;
        Ok(Self { pool })
    }
}

impl SqlxCatalogStore {
    async fn run<T, F, Fut>(&self, operation: F) -> ReferenceResult<T>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: Future<Output = sqlx::Result<T>>,
    {
        operation(self.pool.clone()).await.map_err(persistence)
    }
}

impl SqlxProviderSyncStore {
    async fn run<T, F, Fut>(&self, operation: F) -> ReferenceResult<T>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: Future<Output = sqlx::Result<T>>,
    {
        operation(self.pool.clone()).await.map_err(persistence)
    }
}

#[async_trait]
impl ProviderSyncStore for SqlxProviderSyncStore {
    async fn load_state(
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
        .await
    }

    async fn save_state(
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
        .await
    }

    async fn load_last_good(&mut self, provider: &str) -> ReferenceResult<Option<ProviderCatalog>> {
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
        .await
    }

    async fn save_last_good(
        &mut self,
        provider: &str,
        catalog: &ProviderCatalog,
    ) -> ReferenceResult<()> {
        let payload = serde_json::to_string(catalog).map_err(persistence)?;
        self.run(|pool| async move {
            sqlx::query("INSERT INTO reference_provider_sync(provider, last_good_catalog, updated_at_unix_nanos) VALUES (?, ?, ?) ON CONFLICT(provider) DO UPDATE SET last_good_catalog = excluded.last_good_catalog, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(provider).bind(payload).bind(crate::domain::unix_nanos().get() as i64)
                .execute(&pool).await?;
            Ok(())
        })
        .await
    }

    async fn append_staged_page(
        &mut self,
        provider: &str,
        cursor: Option<&str>,
        catalog: &ProviderCatalog,
    ) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        let cursor = cursor.map(ToOwned::to_owned);
        let payload = serde_json::to_string(catalog).map_err(persistence)?;
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            let ordinal = sqlx::query_scalar::<_, i64>(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM reference_provider_staging WHERE provider = ?",
            )
            .bind(&provider)
            .fetch_one(&mut *tx)
            .await?;
            sqlx::query(
                "INSERT INTO reference_provider_staging(provider, ordinal, payload) VALUES (?, ?, ?)",
            )
            .bind(&provider)
            .bind(ordinal)
            .bind(payload)
            .execute(&mut *tx)
            .await?;
            sqlx::query("INSERT INTO reference_provider_sync(provider, cursor, accumulated_catalog, updated_at_unix_nanos) VALUES (?, ?, NULL, ?) ON CONFLICT(provider) DO UPDATE SET cursor = excluded.cursor, accumulated_catalog = NULL, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(&provider)
                .bind(cursor)
                .bind(crate::domain::unix_nanos().get() as i64)
                .execute(&mut *tx)
                .await?;
            tx.commit().await
        })
        .await
    }

    async fn staged_pages(&mut self, provider: &str) -> ReferenceResult<Vec<ProviderCatalog>> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let rows = sqlx::query(
                "SELECT payload FROM reference_provider_staging WHERE provider = ? ORDER BY ordinal",
            )
            .bind(provider)
            .fetch_all(&pool)
            .await?;
            rows.into_iter()
                .map(|row| {
                    decode(row.try_get::<String, _>("payload")?)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))
                })
                .collect()
        })
        .await
    }

    async fn clear_staged_pages(&mut self, provider: &str) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            sqlx::query("DELETE FROM reference_provider_staging WHERE provider = ?")
                .bind(&provider)
                .execute(&mut *tx)
                .await?;
            sqlx::query("INSERT INTO reference_provider_sync(provider, cursor, accumulated_catalog, updated_at_unix_nanos) VALUES (?, NULL, NULL, ?) ON CONFLICT(provider) DO UPDATE SET cursor = NULL, accumulated_catalog = NULL, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(&provider)
                .bind(crate::domain::unix_nanos().get() as i64)
                .execute(&mut *tx)
                .await?;
            tx.commit().await
        })
        .await
    }

    async fn paused_sources(&mut self) -> ReferenceResult<Vec<String>> {
        self.run(|pool| async move {
            sqlx::query_scalar::<_, String>(
                "SELECT provider FROM reference_provider_control WHERE paused = 1 ORDER BY provider",
            )
            .fetch_all(&pool)
            .await
        })
        .await
    }

    async fn set_source_paused(&mut self, provider: &str, paused: bool) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            sqlx::query("INSERT INTO reference_provider_control(provider, paused, updated_at_unix_nanos) VALUES (?, ?, ?) ON CONFLICT(provider) DO UPDATE SET paused = excluded.paused, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(provider)
                .bind(i64::from(paused))
                .bind(crate::domain::unix_nanos().get() as i64)
                .execute(&pool)
                .await?;
            Ok(())
        })
        .await
    }

    async fn option_underlyings(&mut self, provider: &str) -> ReferenceResult<Vec<String>> {
        let provider = provider.to_owned();
        self.run(|pool| async move {
            sqlx::query_scalar::<_, String>(
                "SELECT underlying FROM reference_option_coverage WHERE provider = ? AND enabled = 1 ORDER BY underlying",
            )
            .bind(provider)
            .fetch_all(&pool)
            .await
        })
        .await
    }

    async fn set_option_underlying(
        &mut self,
        provider: &str,
        underlying: &str,
        enabled: bool,
    ) -> ReferenceResult<()> {
        let provider = provider.to_owned();
        let underlying = underlying.to_owned();
        self.run(|pool| async move {
            sqlx::query("INSERT INTO reference_option_coverage(provider, underlying, enabled, updated_at_unix_nanos) VALUES (?, ?, ?, ?) ON CONFLICT(provider, underlying) DO UPDATE SET enabled = excluded.enabled, updated_at_unix_nanos = excluded.updated_at_unix_nanos")
                .bind(provider)
                .bind(underlying)
                .bind(i64::from(enabled))
                .bind(crate::domain::unix_nanos().get() as i64)
                .execute(&pool)
                .await?;
            Ok(())
        })
        .await
    }
}

#[async_trait]
impl CatalogStore for SqlxCatalogStore {
    async fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
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
        .await
    }

    async fn save_refresh(
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
        let result = self
            .run(|pool| async move {
            let mut tx = pool.begin().await?;
            sqlx::query("INSERT INTO reference_catalog(id, payload) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload = excluded.payload").bind(state_payload).execute(&mut *tx).await?;
            let mut inserted_outbox_rows = 0_i64;
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
                inserted_outbox_rows += sqlx::query("INSERT OR IGNORE INTO reference_pending_publication(event_id,payload) VALUES (?,?)").bind(&event.event_id).bind(payload).execute(&mut *tx).await?.rows_affected() as i64;
            }
            if inserted_outbox_rows > 0 {
                sqlx::query("UPDATE reference_outbox_state SET pending_count = pending_count + ? WHERE id = 1")
                    .bind(inserted_outbox_rows)
                    .execute(&mut *tx)
                    .await?;
            }
            tx.commit().await
            })
            .await;
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

    async fn pending_events(&mut self, limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.payloads(
            "SELECT payload FROM reference_pending_publication ORDER BY rowid LIMIT ?",
            limit as i64,
        )
        .await
    }
    async fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        self.run(|pool| async move {
            Ok(sqlx::query_scalar::<_, i64>(
                "SELECT pending_count FROM reference_outbox_state WHERE id = 1",
            )
            .fetch_one(&pool)
            .await? as usize)
        })
        .await
    }
    async fn lifecycle_events(
        &mut self,
        from: Option<u64>,
        to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.lifecycle_payloads(from, to, None, None, limit).await
    }
    async fn lifecycle_events_filtered(
        &mut self,
        from: Option<u64>,
        to: Option<u64>,
        time_from: Option<u64>,
        time_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.lifecycle_payloads(from, to, time_from, time_to, limit)
            .await
    }
    async fn acknowledge_pending_events(&mut self, event_ids: &[String]) -> ReferenceResult<()> {
        let ids = event_ids.to_vec();
        self.run(|pool| async move {
            let mut tx = pool.begin().await?;
            let mut deleted = 0_i64;
            for chunk in ids.chunks(500) {
                let mut query = QueryBuilder::<Sqlite>::new(
                    "DELETE FROM reference_pending_publication WHERE event_id IN (",
                );
                let mut separated = query.separated(", ");
                for id in chunk {
                    separated.push_bind(id);
                }
                separated.push_unseparated(")");
                deleted += query.build().execute(&mut *tx).await?.rows_affected() as i64;
            }
            if deleted > 0 {
                sqlx::query("UPDATE reference_outbox_state SET pending_count = MAX(0, pending_count - ?) WHERE id = 1")
                    .bind(deleted)
                    .execute(&mut *tx)
                    .await?;
            }
            tx.commit().await
        })
        .await
    }
}

impl SqlxCatalogStore {
    async fn payloads(
        &self,
        query: &'static str,
        limit: i64,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        self.run(|pool| async move {
            let rows = sqlx::query(query).bind(limit).fetch_all(&pool).await?;
            rows.into_iter()
                .map(|row| {
                    decode(row.try_get("payload")?)
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))
                })
                .collect()
        })
        .await
    }
    async fn lifecycle_payloads(
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
        .await
    }
}
