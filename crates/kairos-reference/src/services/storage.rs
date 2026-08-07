//! SQLite storage for the Reference aggregate.

use std::path::{Path, PathBuf};

use rusqlite::{params, Connection, OptionalExtension};

use crate::application::protocol::CatalogStore;
use crate::domain::{ReferenceCatalog, ReferenceError, ReferenceResult};

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
                 CREATE TABLE IF NOT EXISTS reference_lifecycle (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL);",
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
        transaction
            .execute("DELETE FROM reference_lifecycle", [])
            .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        for (sequence, event) in catalog.lifecycle_events.iter().enumerate() {
            let payload = serde_json::to_string(event)
                .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
            transaction
                .execute(
                    "INSERT INTO reference_lifecycle (sequence, payload) VALUES (?1, ?2)",
                    params![sequence as u64 + 1, payload],
                )
                .map_err(|e| ReferenceError::Persistence(e.to_string()))?;
        }
        transaction
            .commit()
            .map_err(|e| ReferenceError::Persistence(e.to_string()))
    }
}
