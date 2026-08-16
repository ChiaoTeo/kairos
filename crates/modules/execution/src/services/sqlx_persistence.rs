//! SQLx-backed Execution state persistence.
//!
//! The public service boundary is currently synchronous because the Execution
//! actor owns a synchronous state transition API. SQLx still performs all
//! database I/O asynchronously on one long-lived current-thread Tokio runtime
//! owned by the Store. This keeps the migration incremental and prevents a
//! database executor from becoming a second business-state owner.

use std::path::Path;

use sqlx::{Row, SqlitePool};

use crate::application::{ExecutionEvent, ExecutionSnapshot, IntentEvent};
use crate::services::persistence::{
    ExecutionOutboxEntry, ExecutionOutboxEvent, ExecutionStateStore,
};

pub struct SqlxExecutionStore {
    pool: SqlitePool,
    runtime: tokio::runtime::Runtime,
}

impl SqlxExecutionStore {
    pub fn new(path: impl AsRef<Path>) -> Result<Self, String> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let url = format!("sqlite://{}?mode=rwc", path.display());
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|error| error.to_string())?;
        let pool: SqlitePool = runtime.block_on(async move {
            let pool = sqlx::sqlite::SqlitePoolOptions::new()
                .max_connections(1)
                .acquire_timeout(std::time::Duration::from_secs(5))
                .connect(&url)
                .await
                .map_err(|error| error.to_string())?;
            sqlx::query("PRAGMA journal_mode = WAL")
                .execute(&pool)
                .await
                .map_err(|error| error.to_string())?;
            sqlx::query("PRAGMA synchronous = NORMAL")
                .execute(&pool)
                .await
                .map_err(|error| error.to_string())?;
            sqlx::query("PRAGMA busy_timeout = 5000")
                .execute(&pool)
                .await
                .map_err(|error| error.to_string())?;
            sqlx::migrate!("./migrations")
                .run(&pool)
                .await
                .map_err(|error| error.to_string())?;
            Ok::<SqlitePool, String>(pool)
        })?;
        Ok(Self { pool, runtime })
    }

    fn execute<F, T>(&self, operation: F) -> Result<T, String>
    where
        F: for<'a> FnOnce(
            &'a SqlitePool,
        ) -> futures_util::future::BoxFuture<'a, Result<T, sqlx::Error>>,
        T: Send + 'static,
    {
        let pool = self.pool.clone();
        let run = async move { operation(&pool).await.map_err(|error| error.to_string()) };
        if tokio::runtime::Handle::try_current().is_ok() {
            tokio::task::block_in_place(|| self.runtime.block_on(run))
        } else {
            self.runtime.block_on(run)
        }
    }

    fn checkpoint(&self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        let payload = serde_json::to_vec(snapshot).map_err(|error| error.to_string())?;
        let generation = snapshot.generation.get() as i64;
        let event_sequence = snapshot.event_sequence.get() as i64;
        self.execute(|pool| {
            Box::pin(async move {
                sqlx::query("INSERT INTO execution_checkpoints (generation, event_sequence, payload, created_at_unix_nanos) VALUES (?, ?, ?, ?)")
                    .bind(generation).bind(event_sequence).bind(payload).bind(now_nanos() as i64)
                    .execute(pool).await?;
                Ok(())
            })
        })
    }

    fn commit<T: serde::Serialize>(
        &self,
        kind: &str,
        event: &T,
        snapshot: &ExecutionSnapshot,
    ) -> Result<(), String> {
        let payload = serde_json::to_vec(event).map_err(|error| error.to_string())?;
        let event_key = String::from_utf8_lossy(&payload).into_owned();
        let kind = kind.to_string();
        let generation = snapshot.generation.get() as i64;
        let event_sequence = snapshot.event_sequence.get() as i64;
        let checkpoint = serde_json::to_vec(snapshot).map_err(|error| error.to_string())?;
        self.execute(|pool| {
            Box::pin(async move {
                let mut tx = pool.begin().await?;
                sqlx::query("INSERT OR IGNORE INTO execution_outbox (event_key, event_kind, payload, created_at_unix_nanos) VALUES (?, ?, ?, ?)")
                    .bind(event_key).bind(kind).bind(payload).bind(now_nanos() as i64).execute(&mut *tx).await?;
                sqlx::query("INSERT INTO execution_checkpoints (generation, event_sequence, payload, created_at_unix_nanos) VALUES (?, ?, ?, ?)")
                    .bind(generation).bind(event_sequence).bind(checkpoint).bind(now_nanos() as i64).execute(&mut *tx).await?;
                tx.commit().await
            })
        })
    }

    fn append<T: serde::Serialize>(&self, kind: &str, event: &T) -> Result<(), String> {
        let payload = serde_json::to_vec(event).map_err(|error| error.to_string())?;
        let event_key = String::from_utf8_lossy(&payload).into_owned();
        let kind = kind.to_string();
        let result = self.execute(|pool| Box::pin(async move {
            sqlx::query("INSERT OR IGNORE INTO execution_outbox (event_key, event_kind, payload, created_at_unix_nanos) VALUES (?, ?, ?, ?)")
                .bind(event_key).bind(kind).bind(payload).bind(now_nanos() as i64).execute(pool).await?;
            Ok(())
        }));
        if result.is_ok() {
            kairos_workspace::logging::record_counter("kairos.execution.outbox.append", 1);
        }
        result
    }
}

impl ExecutionStateStore for SqlxExecutionStore {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String> {
        self.execute(|pool| {
            Box::pin(async move {
                let row = sqlx::query(
                    "SELECT payload FROM execution_checkpoints ORDER BY checkpoint_id DESC LIMIT 1",
                )
                .fetch_optional(pool)
                .await?;
                row.map(|row| {
                    let payload = row.try_get::<Vec<u8>, _>("payload")?;
                    serde_json::from_slice(&payload)
                        .map_err(|error| sqlx::Error::Decode(Box::new(error)))
                })
                .transpose()
            })
        })
    }

    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        self.checkpoint(snapshot)
    }

    fn commit_event(
        &mut self,
        event: &ExecutionEvent,
        snapshot: &ExecutionSnapshot,
    ) -> Result<(), String> {
        self.commit("order", event, snapshot)
    }

    fn commit_intent_event(
        &mut self,
        event: &IntentEvent,
        snapshot: &ExecutionSnapshot,
    ) -> Result<(), String> {
        self.commit("intent", event, snapshot)
    }

    fn append_event(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        self.append("order", event)
    }

    fn append_intent_event(&mut self, event: &IntentEvent) -> Result<(), String> {
        self.append("intent", event)
    }

    fn pending_outbox(&mut self, limit: u32) -> Result<Vec<ExecutionOutboxEntry>, String> {
        self.execute(move |pool| Box::pin(async move {
            let rows = sqlx::query("SELECT outbox_id, event_kind, payload, created_at_unix_nanos FROM execution_outbox ORDER BY outbox_id ASC LIMIT ?")
                .bind(limit.max(1) as i64).fetch_all(pool).await?;
            rows.into_iter().map(|row| {
                let id = row.try_get::<i64, _>("outbox_id")? as u64;
                let created_at_unix_nanos = row.try_get::<i64, _>("created_at_unix_nanos")? as u64;
                let kind = row.try_get::<String, _>("event_kind")?;
                let payload = row.try_get::<Vec<u8>, _>("payload")?;
                let event = match kind.as_str() {
                    "order" => ExecutionOutboxEvent::Order(serde_json::from_slice(&payload).map_err(|error| sqlx::Error::Decode(Box::new(error)))?),
                    "intent" => ExecutionOutboxEvent::Intent(serde_json::from_slice(&payload).map_err(|error| sqlx::Error::Decode(Box::new(error)))?),
                    other => return Err(sqlx::Error::Protocol(format!("unknown execution outbox event kind: {other}"))),
                };
                Ok(ExecutionOutboxEntry { id, created_at_unix_nanos, event })
            }).collect()
        }))
    }

    fn acknowledge_outbox(&mut self, ids: &[u64]) -> Result<(), String> {
        if ids.is_empty() {
            return Ok(());
        }
        let ids = ids.to_vec();
        let count = ids.len() as u64;
        let result = self.execute(move |pool| {
            Box::pin(async move {
                let mut tx = pool.begin().await?;
                for id in ids {
                    sqlx::query("DELETE FROM execution_outbox WHERE outbox_id = ?")
                        .bind(id as i64)
                        .execute(&mut *tx)
                        .await?;
                }
                tx.commit().await
            })
        });
        if result.is_ok() {
            kairos_workspace::logging::record_counter("kairos.execution.outbox.ack", count);
        }
        result
    }

    fn latest_checkpoint_unix_nanos(&mut self) -> Result<Option<u64>, String> {
        self.execute(|pool| {
            Box::pin(async move {
                let timestamp = sqlx::query_scalar::<_, Option<i64>>(
                    "SELECT MAX(created_at_unix_nanos) FROM execution_checkpoints",
                )
                .fetch_one(pool)
                .await?;
                Ok(timestamp.map(|value| value as u64))
            })
        })
    }
}

fn now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_nanos() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use std::process::Command;

    use super::SqlxExecutionStore;
    use crate::application::ExecutionEvent;
    use crate::domain::ExecutionOrderStatus;
    use crate::services::persistence::ExecutionStateStore;

    #[test]
    fn initializes_sqlx_schema_and_reopens_empty_state() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("execution.sqlite");
        let mut store = SqlxExecutionStore::new(&path).unwrap();
        assert!(store.load().unwrap().is_none());
        assert!(store.pending_outbox(32).unwrap().is_empty());
        assert_eq!(store.latest_checkpoint_unix_nanos().unwrap(), None);

        drop(store);
        let mut reopened = SqlxExecutionStore::new(&path).unwrap();
        assert!(reopened.load().unwrap().is_none());
    }

    #[test]
    fn sqlx_outbox_is_idempotent_and_survives_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("execution.sqlite");
        let event = ExecutionEvent {
            order_id: kairos_primitives::OrderId::new("order-1").unwrap(),
            intent_id: None,
            plan_id: None,
            leg_id: None,
            status: ExecutionOrderStatus::Accepted,
            remote_order_id: Some(kairos_primitives::RemoteOrderId::new("exchange-1").unwrap()),
            occurred_at_unix_nanos: 42.into(),
            reason: String::new(),
            fill_id: None,
            filled_quantity: None,
        };
        let mut store = SqlxExecutionStore::new(&path).unwrap();
        store.append_event(&event).unwrap();
        store.append_event(&event).unwrap();
        assert_eq!(store.pending_outbox(32).unwrap().len(), 1);
        drop(store);

        let mut reopened = SqlxExecutionStore::new(&path).unwrap();
        let pending = reopened.pending_outbox(32).unwrap();
        assert_eq!(pending.len(), 1);
        reopened.acknowledge_outbox(&[pending[0].id]).unwrap();
        assert!(reopened.pending_outbox(32).unwrap().is_empty());
    }

    #[test]
    fn crash_child_writes_outbox() {
        let Ok(path) = std::env::var("KAIROS_SQLX_CRASH_CHILD_PATH") else {
            return;
        };
        let event = ExecutionEvent {
            order_id: kairos_primitives::OrderId::new("crash-order").unwrap(),
            intent_id: None,
            plan_id: None,
            leg_id: None,
            status: ExecutionOrderStatus::Accepted,
            remote_order_id: None,
            occurred_at_unix_nanos: 42.into(),
            reason: String::new(),
            fill_id: None,
            filled_quantity: None,
        };
        let mut store = SqlxExecutionStore::new(path).unwrap();
        store.append_event(&event).unwrap();
        std::process::abort();
    }

    #[test]
    fn sqlx_outbox_survives_real_process_crash() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("crash-recovery.sqlite");
        let status = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "services::sqlx_persistence::tests::crash_child_writes_outbox",
                "--nocapture",
            ])
            .env("KAIROS_SQLX_CRASH_CHILD_PATH", &path)
            .status()
            .unwrap();
        assert!(!status.success(), "crash child unexpectedly exited cleanly");

        let mut reopened = SqlxExecutionStore::new(&path).unwrap();
        let pending = reopened.pending_outbox(32).unwrap();
        assert_eq!(pending.len(), 1);
        assert!(matches!(
            pending[0].event,
            crate::services::persistence::ExecutionOutboxEvent::Order(_)
        ));
    }
}
