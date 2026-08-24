use std::path::Path;
use std::sync::OnceLock;

use sqlx::SqlitePool;
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};

use crate::domain::ReferenceError;

static SQLITE_OPERATION_LOCK: OnceLock<tokio::sync::Mutex<()>> = OnceLock::new();

pub(crate) fn persistence(error: impl std::fmt::Display) -> ReferenceError {
    ReferenceError::Persistence(error.to_string())
}

pub(crate) fn operation_lock() -> &'static tokio::sync::Mutex<()> {
    SQLITE_OPERATION_LOCK.get_or_init(|| tokio::sync::Mutex::new(()))
}

pub(crate) async fn open_pool(path: &Path) -> sqlx::Result<SqlitePool> {
    let options = SqliteConnectOptions::new()
        .filename(path)
        .create_if_missing(true)
        // Canonical reconciliation uses temporary relational working sets.
        // Keep them file-backed and cap each connection's page cache so a
        // million-row refresh cannot silently turn into a process-sized heap.
        .pragma("temp_store", "FILE")
        .pragma("cache_size", "-32768")
        .pragma("busy_timeout", "5000");
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
    let has_meta = sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='reference_meta'",
    )
    .fetch_one(&pool)
    .await?
        != 0;
    if has_meta {
        let version =
            sqlx::query_scalar::<_, i64>("SELECT schema_version FROM reference_meta WHERE id = 1")
                .fetch_optional(&pool)
                .await?
                .unwrap_or_default();
        let expected = i64::from(kairos_reference_contract::REFERENCE_SQLITE_SCHEMA_VERSION);
        if version != expected {
            return Err(sqlx::Error::Protocol(format!(
                "unsupported Reference SQLite schema version {version}; expected {expected}"
            )));
        }
    }
    sqlx::raw_sql(include_str!("../../../schema.sql"))
        .execute(&pool)
        .await?;
    ensure_provider_control_desired_state(&pool).await?;
    Ok(pool)
}

async fn ensure_provider_control_desired_state(pool: &SqlitePool) -> sqlx::Result<()> {
    let has_desired_state = sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM pragma_table_info('reference_provider_control') WHERE name = 'desired_state'",
    )
    .fetch_one(pool)
    .await?
        != 0;
    if !has_desired_state {
        sqlx::query(
            "ALTER TABLE reference_provider_control ADD COLUMN desired_state TEXT NOT NULL DEFAULT 'enabled' CHECK (desired_state IN ('enabled', 'disabled', 'paused', 'removed'))",
        )
        .execute(pool)
        .await?;
    }
    sqlx::query(
        "UPDATE reference_provider_control SET desired_state = CASE WHEN paused = 1 THEN 'paused' ELSE desired_state END",
    )
    .execute(pool)
    .await?;
    Ok(())
}
