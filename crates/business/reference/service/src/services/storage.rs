//! SQLite storage for the Reference aggregate.

use std::path::Path;

use rusqlite::{params, Connection, OptionalExtension};

use crate::domain::{
    LifecycleEvent, ProviderCatalog, ReferenceCatalog, ReferenceError, ReferenceResult,
};

/// Internal persistence seam. The application owns the use case; storage
/// implementations remain selected by composition.
pub(crate) trait CatalogStore: Send {
    fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>>;
    fn save(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<()>;

    fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
    ) -> ReferenceResult<()> {
        self.save(catalog)?;
        self.enqueue_events(events)
    }

    fn enqueue_events(&mut self, _events: &[LifecycleEvent]) -> ReferenceResult<()> {
        Ok(())
    }

    fn pending_events(&mut self, _limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        Ok(Vec::new())
    }

    fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        Ok(0)
    }

    fn lifecycle_events(
        &mut self,
        _sequence_from: Option<u64>,
        _sequence_to: Option<u64>,
        _limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        Ok(Vec::new())
    }

    fn acknowledge_pending_events(&mut self, _event_ids: &[String]) -> ReferenceResult<()> {
        Ok(())
    }
}

/// Durable provider cursor state. It is deliberately separate from the
/// business catalog: a page cursor is operational progress, not a reference
/// entity, and must survive a process restart without being published.
pub(crate) trait ProviderSyncStore: Send {
    fn load_state(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Option<(Option<String>, Option<ProviderCatalog>)>>;
    fn save_state(
        &mut self,
        provider: &str,
        cursor: Option<&str>,
        accumulated: Option<&ProviderCatalog>,
    ) -> ReferenceResult<()>;
}

pub struct SqliteCatalogStore {
    connection: Option<Connection>,
}

const IN_MEMORY_LIFECYCLE_LIMIT: usize = 4096;

impl SqliteCatalogStore {
    pub fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let path = path.as_ref().to_path_buf();
        let connection =
            Connection::open(&path).map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        connection
            .execute_batch(
                "PRAGMA journal_mode = WAL;
                 PRAGMA synchronous = NORMAL;
                 PRAGMA busy_timeout = 5000;",
            )
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        connection
            .execute_batch(
                "CREATE TABLE IF NOT EXISTS reference_catalog (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL);\
                 CREATE TABLE IF NOT EXISTS reference_lifecycle (sequence INTEGER PRIMARY KEY, event_type TEXT NOT NULL, market_id TEXT, venue_id TEXT, event_time_unix_nanos INTEGER NOT NULL, payload TEXT NOT NULL);
                 CREATE INDEX IF NOT EXISTS reference_lifecycle_event_time_idx ON reference_lifecycle(event_time_unix_nanos);
                 CREATE INDEX IF NOT EXISTS reference_lifecycle_market_idx ON reference_lifecycle(market_id);
                 CREATE INDEX IF NOT EXISTS reference_lifecycle_type_idx ON reference_lifecycle(event_type);
                 CREATE TABLE IF NOT EXISTS reference_pending_publication (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL);",
            )
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        Ok(Self {
            connection: Some(connection),
        })
    }
}

pub(crate) struct SqliteProviderSyncStore {
    connection: Connection,
}

impl SqliteProviderSyncStore {
    pub(crate) fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let connection = Connection::open(path.as_ref())
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        connection
            .execute_batch(
                "PRAGMA journal_mode = WAL;
                 PRAGMA synchronous = NORMAL;
                 PRAGMA busy_timeout = 5000;
                 CREATE TABLE IF NOT EXISTS reference_provider_sync (
                     provider TEXT PRIMARY KEY,
                     cursor TEXT,
                     accumulated_catalog TEXT,
                     updated_at_unix_nanos INTEGER NOT NULL
                 );",
            )
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        let has_accumulated_catalog: bool = connection
            .prepare("PRAGMA table_info(reference_provider_sync)")
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?
            .query_map([], |row| row.get::<_, String>(1))
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?
            .filter_map(Result::ok)
            .any(|name| name == "accumulated_catalog");
        if !has_accumulated_catalog {
            connection
                .execute(
                    "ALTER TABLE reference_provider_sync ADD COLUMN accumulated_catalog TEXT",
                    [],
                )
                .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        }
        Ok(Self { connection })
    }
}

impl ProviderSyncStore for SqliteProviderSyncStore {
    fn load_state(
        &mut self,
        provider: &str,
    ) -> ReferenceResult<Option<(Option<String>, Option<ProviderCatalog>)>> {
        let state = self
            .connection
            .query_row(
                "SELECT cursor, accumulated_catalog FROM reference_provider_sync WHERE provider = ?1",
                params![provider],
                |row| {
                    let cursor = row.get(0)?;
                    let payload: Option<String> = row.get(1)?;
                    Ok((cursor, payload))
                },
            )
            .optional()
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        state
            .map(|(cursor, payload)| {
                let accumulated = payload
                    .map(|value| {
                        serde_json::from_str(&value)
                            .map_err(|e| ReferenceError::Persistence(e.to_string()))
                    })
                    .transpose()?;
                Ok((cursor, accumulated))
            })
            .transpose()
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
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        self.connection
            .execute(
                "INSERT INTO reference_provider_sync(provider, cursor, accumulated_catalog, updated_at_unix_nanos)
                 VALUES (?1, ?2, ?3, ?4)
                 ON CONFLICT(provider) DO UPDATE SET cursor = excluded.cursor,
                 accumulated_catalog = excluded.accumulated_catalog,
                 updated_at_unix_nanos = excluded.updated_at_unix_nanos",
                params![provider, cursor, payload, crate::domain::unix_nanos() as i64],
            )
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        Ok(())
    }
}

impl CatalogStore for SqliteCatalogStore {
    fn load(&mut self) -> ReferenceResult<Option<ReferenceCatalog>> {
        let connection = self.connection.as_ref().expect("sqlite store connection");
        let payload: Option<String> = connection
            .query_row(
                "SELECT payload FROM reference_catalog WHERE id = 1",
                [],
                |row| row.get(0),
            )
            .optional()
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        payload
            .map(|value| {
                let mut catalog: ReferenceCatalog = serde_json::from_str(&value)
                    .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
                let mut statement = connection
                    .prepare(
                        "SELECT payload FROM reference_lifecycle ORDER BY sequence DESC LIMIT ?1",
                    )
                    .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
                let rows = statement
                    .query_map(params![IN_MEMORY_LIFECYCLE_LIMIT as i64], |row| {
                        row.get::<_, String>(0)
                    })
                    .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
                catalog.lifecycle_events = rows
                    .map(|row| {
                        let payload =
                            row.map_err(|e| ReferenceError::Persistence(e.to_string()))?;
                        serde_json::from_str(&payload)
                            .map_err(|e| ReferenceError::Persistence(e.to_string()))
                    })
                    .collect::<ReferenceResult<Vec<LifecycleEvent>>>()?;
                catalog.lifecycle_events.reverse();
                Ok(catalog)
            })
            .transpose()
    }

    fn save(&mut self, catalog: &ReferenceCatalog) -> ReferenceResult<()> {
        self.save_refresh(catalog, &[])
    }

    fn save_refresh(
        &mut self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
    ) -> ReferenceResult<()> {
        let started = std::time::Instant::now();
        let connection = self.connection.as_mut().expect("sqlite store connection");
        let transaction = connection
            .transaction()
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        // Lifecycle history is append-only and lives in reference_lifecycle;
        // never serialize and rewrite the complete history on every refresh.
        let mut current_state = catalog.clone();
        current_state.lifecycle_events.clear();
        let payload = serde_json::to_string(&current_state)
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        transaction
            .execute(
                "INSERT INTO reference_catalog (id, payload) VALUES (1, ?1) ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                params![payload],
            )
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        for (offset, event) in events.iter().enumerate() {
            let payload = serde_json::to_string(event)
                .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
            transaction
                .execute(
                    "INSERT OR IGNORE INTO reference_lifecycle (sequence, event_type, market_id, venue_id, event_time_unix_nanos, payload) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![
                        event_sequence(event).unwrap_or_else(|| {
                            catalog
                                .event_sequence
                                .saturating_sub(events.len() as u64)
                                .saturating_add(1 + offset as u64)
                        }),
                        &event.event_type,
                        event.market_id.as_deref(),
                        event.venue_id.as_deref(),
                        event.event_time_unix_nanos as i64,
                        payload
                    ],
                )
                .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        }
        for event in events {
            let payload = serde_json::to_string(event)
                .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
            transaction
                .execute(
                    "INSERT OR IGNORE INTO reference_pending_publication (event_id, payload) VALUES (?1, ?2)",
                    params![event.event_id, payload],
                )
                .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        }
        transaction
            .commit()
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        tracing::info!(
            event = "reference_state_commit_completed",
            component = "reference",
            generation = catalog.generation,
            event_count = events.len(),
            duration_ms = started.elapsed().as_millis() as u64,
            "reference state commit completed"
        );
        Ok(())
    }

    fn pending_events(&mut self, limit: usize) -> ReferenceResult<Vec<LifecycleEvent>> {
        let connection = self.connection.as_ref().expect("sqlite store connection");
        let mut statement = connection
            .prepare("SELECT payload FROM reference_pending_publication ORDER BY rowid LIMIT ?1")
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        let rows = statement
            .query_map(params![limit as i64], |row| row.get::<_, String>(0))
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        rows.map(|row| {
            let payload = row.map_err(|e| ReferenceError::Persistence(e.to_string()))?;
            serde_json::from_str(&payload).map_err(|e| ReferenceError::Persistence(e.to_string()))
        })
        .collect()
    }

    fn pending_event_count(&mut self) -> ReferenceResult<usize> {
        let connection = self.connection.as_ref().expect("sqlite store connection");
        connection
            .query_row(
                "SELECT COUNT(*) FROM reference_pending_publication",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map(|value| value.max(0) as usize)
            .map_err(|e| ReferenceError::Persistence(e.to_string()))
    }

    fn lifecycle_events(
        &mut self,
        sequence_from: Option<u64>,
        sequence_to: Option<u64>,
        limit: usize,
    ) -> ReferenceResult<Vec<LifecycleEvent>> {
        let connection = self.connection.as_ref().expect("sqlite store connection");
        let lower = sequence_from.unwrap_or(1) as i64;
        let upper = sequence_to.map(|value| value as i64).unwrap_or(i64::MAX);
        let mut statement = connection
            .prepare("SELECT payload FROM reference_lifecycle WHERE sequence >= ?1 AND sequence <= ?2 ORDER BY sequence LIMIT ?3")
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        let rows = statement
            .query_map(params![lower, upper, limit as i64], |row| {
                row.get::<_, String>(0)
            })
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        rows.map(|row| {
            let payload = row.map_err(|e| ReferenceError::Persistence(e.to_string()))?;
            serde_json::from_str(&payload).map_err(|e| ReferenceError::Persistence(e.to_string()))
        })
        .collect()
    }

    fn acknowledge_pending_events(&mut self, event_ids: &[String]) -> ReferenceResult<()> {
        let connection = self.connection.as_mut().expect("sqlite store connection");
        let transaction = connection
            .transaction()
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        for event_id in event_ids {
            transaction
                .execute(
                    "DELETE FROM reference_pending_publication WHERE event_id = ?1",
                    params![event_id],
                )
                .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        }
        transaction
            .commit()
            .map_err(|e| ReferenceError::Persistence(e.to_string()))
    }
}

fn event_sequence(event: &LifecycleEvent) -> Option<u64> {
    event.event_id.rsplit(':').next()?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::CatalogStore;
    use super::ProviderSyncStore;
    use super::SqliteCatalogStore;
    use super::SqliteProviderSyncStore;
    use crate::domain::{LifecycleEvent, Market, ProviderCatalog, ReferenceCatalog};

    #[test]
    fn sqlite_outbox_survives_store_reopen_until_acknowledged() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let event = LifecycleEvent {
            event_id: "reference:00000000000000000001".into(),
            event_type: "listed".into(),
            ..Default::default()
        };
        {
            let mut store = SqliteCatalogStore::open(&path).unwrap();
            store
                .save_refresh(&ReferenceCatalog::default(), std::slice::from_ref(&event))
                .unwrap();
        }
        let mut reopened = SqliteCatalogStore::open(&path).unwrap();
        assert_eq!(reopened.pending_events(16).unwrap(), vec![event.clone()]);
        reopened
            .acknowledge_pending_events(&[event.event_id.clone()])
            .unwrap();
        assert!(reopened.pending_events(16).unwrap().is_empty());
    }

    #[test]
    fn sqlite_lifecycle_rows_are_append_only() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let first = LifecycleEvent {
            event_id: "reference:00000000000000000001".into(),
            event_type: "listed".into(),
            ..Default::default()
        };
        let second = LifecycleEvent {
            event_id: "reference:00000000000000000002".into(),
            event_type: "status_changed".into(),
            ..Default::default()
        };
        let mut store = SqliteCatalogStore::open(&path).unwrap();
        let mut catalog = ReferenceCatalog::default();
        catalog.lifecycle_events.push(first.clone());
        store.save_refresh(&catalog, &[first]).unwrap();
        catalog.lifecycle_events.push(second.clone());
        store.save_refresh(&catalog, &[second]).unwrap();
        let connection = rusqlite::Connection::open(&path).unwrap();
        let count: i64 = connection
            .query_row("SELECT COUNT(*) FROM reference_lifecycle", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 2);
    }

    #[test]
    fn provider_sync_state_survives_store_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let catalog = ProviderCatalog {
            markets: vec![Market {
                market_id: "market:one".into(),
                ..Default::default()
            }],
            ..Default::default()
        };
        {
            let mut store = SqliteProviderSyncStore::open(&path).unwrap();
            store
                .save_state("massive-options", Some("cursor-2"), Some(&catalog))
                .unwrap();
        }
        let mut reopened = SqliteProviderSyncStore::open(&path).unwrap();
        let (cursor, accumulated) = reopened.load_state("massive-options").unwrap().unwrap();
        assert_eq!(cursor.as_deref(), Some("cursor-2"));
        assert_eq!(accumulated.unwrap(), catalog);
    }

    #[test]
    fn sqlite_lifecycle_history_supports_bounded_pages() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reference.sqlite");
        let mut store = SqliteCatalogStore::open(&path).unwrap();
        let events = (1..=3)
            .map(|sequence| LifecycleEvent {
                event_id: format!("reference:{sequence:020}"),
                event_type: "listed".into(),
                ..Default::default()
            })
            .collect::<Vec<_>>();
        store
            .save_refresh(&ReferenceCatalog::default(), &events)
            .unwrap();
        let page = store.lifecycle_events(Some(2), Some(3), 1).unwrap();
        assert_eq!(page.len(), 1);
        assert_eq!(page[0].event_id, "reference:00000000000000000002");
    }
}
