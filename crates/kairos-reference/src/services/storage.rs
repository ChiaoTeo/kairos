//! SQLite storage for the Reference aggregate.

use std::path::{Path, PathBuf};

use rusqlite::{params, Connection, OptionalExtension};

use crate::application::protocol::CatalogStore;
use crate::domain::{LifecycleEvent, ReferenceCatalog, ReferenceError, ReferenceResult};

pub struct SqliteCatalogStore {
    path: PathBuf,
    connection: Option<Connection>,
}

impl SqliteCatalogStore {
    pub fn open(path: impl AsRef<Path>) -> ReferenceResult<Self> {
        let path = path.as_ref().to_path_buf();
        let connection =
            Connection::open(&path).map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        connection
            .execute_batch(
                "CREATE TABLE IF NOT EXISTS reference_catalog (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL);\
                 CREATE TABLE IF NOT EXISTS reference_lifecycle (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                 CREATE TABLE IF NOT EXISTS reference_pending_publication (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL);",
            )
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        Ok(Self {
            path,
            connection: Some(connection),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
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
                serde_json::from_str(&value).map_err(|e| ReferenceError::Persistence(e.to_string()))
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
        let connection = self.connection.as_mut().expect("sqlite store connection");
        let transaction = connection
            .transaction()
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        let payload = serde_json::to_string(catalog)
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        transaction
            .execute(
                "INSERT INTO reference_catalog (id, payload) VALUES (1, ?1) ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                params![payload],
            )
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        for (sequence, event) in catalog.lifecycle_events.iter().enumerate() {
            let payload = serde_json::to_string(event)
                .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
            transaction
                .execute(
                    "INSERT OR IGNORE INTO reference_lifecycle (sequence, payload) VALUES (?1, ?2)",
                    params![sequence as u64 + 1, payload],
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
            .map_err(|e| ReferenceError::Persistence(e.to_string()))
    }

    fn pending_events(&mut self) -> ReferenceResult<Vec<LifecycleEvent>> {
        let connection = self.connection.as_ref().expect("sqlite store connection");
        let mut statement = connection
            .prepare("SELECT payload FROM reference_pending_publication ORDER BY rowid")
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        let rows = statement
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        rows.map(|row| {
            let payload = row.map_err(|e| ReferenceError::Persistence(e.to_string()))?;
            serde_json::from_str(&payload).map_err(|e| ReferenceError::Persistence(e.to_string()))
        })
        .collect()
    }

    fn acknowledge_pending_events(&mut self) -> ReferenceResult<()> {
        let connection = self.connection.as_mut().expect("sqlite store connection");
        connection
            .execute("DELETE FROM reference_pending_publication", [])
            .map(|_| ())
            .map_err(|e| ReferenceError::Persistence(e.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::SqliteCatalogStore;
    use crate::application::CatalogStore;
    use crate::domain::{LifecycleEvent, ReferenceCatalog};

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
        assert_eq!(reopened.pending_events().unwrap(), vec![event]);
        reopened.acknowledge_pending_events().unwrap();
        assert!(reopened.pending_events().unwrap().is_empty());
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
}
