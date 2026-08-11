//! SQLx-backed execution audit persistence.

use std::path::Path;

use sqlx::{
    sqlite::{SqliteConnectOptions, SqlitePoolOptions},
    Row, SqlitePool,
};

use crate::application::{
    remote_status, ExecutionAuditEvent, ExecutionAuditQuery, ExecutionAuditSink, ExecutionEvent,
    IntentEvent,
};

pub struct SqlxExecutionAudit {
    pool: SqlitePool,
    runtime: tokio::runtime::Runtime,
}

impl SqlxExecutionAudit {
    pub fn new(path: impl AsRef<Path>) -> Result<Self, String> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let runtime = runtime()?;
        let pool = runtime
            .block_on(async move {
                let options = SqliteConnectOptions::new()
                    .filename(path)
                    .create_if_missing(true);
                let pool = SqlitePoolOptions::new()
                    .max_connections(1)
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
                Ok::<_, sqlx::Error>(pool)
            })
            .map_err(|error| error.to_string())?;
        Ok(Self { pool, runtime })
    }

    fn run<T, F, Fut>(&self, operation: F) -> Result<T, String>
    where
        F: FnOnce(SqlitePool) -> Fut,
        Fut: std::future::Future<Output = sqlx::Result<T>>,
    {
        self.runtime
            .block_on(operation(self.pool.clone()))
            .map_err(|error| error.to_string())
    }

    pub fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intents: &[IntentEvent],
    ) -> Result<(), String> {
        if events.is_empty() && intents.is_empty() {
            return Ok(());
        }
        let events = events.to_vec();
        let intents = intents.to_vec();
        self.run(|pool| async move {
            let mut transaction = pool.begin().await?;
            for event in &events {
                sqlx::query("INSERT OR IGNORE INTO execution_events(order_id,status,remote_order_id,occurred_at_unix_nanos,reason,event_key) VALUES (?,?,?,?,?,?)")
                    .bind(event.order_id.as_str())
                    .bind(format!("{:?}", event.status).to_ascii_lowercase())
                    .bind(event.remote_order_id.as_ref().map(kairos_domain_types::RemoteOrderId::as_str))
                    .bind(event.occurred_at_unix_nanos.get() as i64)
                    .bind(&event.reason)
                    .bind(order_event_key(event))
                    .execute(&mut *transaction)
                    .await?;
            }
            for event in &intents {
                let order_ids = serde_json::to_string(&event.order_ids)
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                sqlx::query("INSERT OR IGNORE INTO intent_events(intent_id,status,order_ids,completed_quantity_mantissa,completed_quantity_scale,occurred_at_unix_nanos,reason,event_key) VALUES (?,?,?,?,?,?,?,?)")
                    .bind(event.intent_id.to_string())
                    .bind(format!("{:?}", event.status).to_ascii_lowercase())
                    .bind(order_ids)
                    .bind(event.completed_quantity.mantissa())
                    .bind(event.completed_quantity.scale())
                    .bind(event.occurred_at_unix_nanos.get() as i64)
                    .bind(intent_event_key(event))
                    .execute(&mut *transaction)
                    .await?;
            }
            transaction.commit().await
        })
    }

    pub fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        self.publish_batch(std::slice::from_ref(event), &[])
    }

    pub fn publish_intent(&mut self, event: &IntentEvent) -> Result<(), String> {
        self.publish_batch(&[], std::slice::from_ref(event))
    }

    pub fn query(
        &mut self,
        query: &ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, String> {
        let order_id = query.order_id.as_ref().map(ToString::to_string);
        let remote_order_id = query.remote_order_id.as_ref().map(ToString::to_string);
        let status = query.status.clone();
        let since = query.since_unix_nanos.map(|value| value.get() as i64);
        let until = query.until_unix_nanos.map(|value| value.get() as i64);
        let limit = query.limit.unwrap_or(10_000) as i64;
        self.run(|pool| async move {
            let rows = sqlx::query("SELECT sequence,order_id,status,remote_order_id,occurred_at_unix_nanos,reason FROM execution_events WHERE (? IS NULL OR order_id = ?) AND (? IS NULL OR remote_order_id = ?) AND (? IS NULL OR lower(status) = lower(?)) AND (? IS NULL OR occurred_at_unix_nanos >= ?) AND (? IS NULL OR occurred_at_unix_nanos <= ?) ORDER BY sequence ASC LIMIT ?")
                .bind(&order_id).bind(&order_id)
                .bind(&remote_order_id).bind(&remote_order_id)
                .bind(&status).bind(&status)
                .bind(since).bind(since).bind(until).bind(until).bind(limit)
                .fetch_all(&pool)
                .await?;
            rows.into_iter()
                .map(|row| {
                    let order_id = kairos_domain_types::OrderId::new(
                        row.try_get::<String, _>("order_id")?,
                    )
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                    let remote_order_id = row
                        .try_get::<Option<String>, _>("remote_order_id")?
                        .map(kairos_domain_types::RemoteOrderId::new)
                        .transpose()
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                    Ok(ExecutionAuditEvent {
                        sequence: (row.try_get::<i64, _>("sequence")? as u64).into(),
                        order_id,
                        status: remote_status(&row.try_get::<String, _>("status")?),
                        remote_order_id,
                        occurred_at_unix_nanos: (row
                            .try_get::<i64, _>("occurred_at_unix_nanos")?
                            as u64)
                            .into(),
                        reason: row.try_get("reason")?,
                    })
                })
                .collect()
        })
    }
}

impl ExecutionAuditSink for SqlxExecutionAudit {
    fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        Self::publish(self, event)
    }
    fn publish_intent(&mut self, event: &IntentEvent) -> Result<(), String> {
        Self::publish_intent(self, event)
    }
    fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intents: &[IntentEvent],
    ) -> Result<(), String> {
        Self::publish_batch(self, events, intents)
    }
    fn query(&mut self, query: &ExecutionAuditQuery) -> Result<Vec<ExecutionAuditEvent>, String> {
        Self::query(self, query)
    }
}

fn runtime() -> Result<tokio::runtime::Runtime, String> {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|error| error.to_string())
}

fn order_event_key(event: &ExecutionEvent) -> String {
    format!(
        "{}|{:?}|{}|{}|{}",
        event.order_id,
        event.status,
        event
            .remote_order_id
            .as_ref()
            .map(kairos_domain_types::RemoteOrderId::as_str)
            .unwrap_or_default(),
        event.occurred_at_unix_nanos,
        event.reason
    )
}

fn intent_event_key(event: &IntentEvent) -> String {
    format!(
        "{}|{:?}|{}|{}|{}|{}",
        event.intent_id,
        event.status,
        event.occurred_at_unix_nanos,
        event.completed_quantity.mantissa(),
        event.completed_quantity.scale(),
        event.reason
    )
}

#[cfg(test)]
mod tests {
    use super::SqlxExecutionAudit;
    use crate::application::{ExecutionAuditQuery, ExecutionEvent};
    use crate::domain::ExecutionOrderStatus;

    #[test]
    fn sqlx_audit_is_idempotent_and_queryable() {
        let directory = tempfile::tempdir().unwrap();
        let mut audit = SqlxExecutionAudit::new(directory.path().join("audit.sqlite")).unwrap();
        let event = ExecutionEvent {
            order_id: kairos_domain_types::OrderId::new("order-1").unwrap(),
            intent_id: None,
            plan_id: None,
            leg_id: None,
            status: ExecutionOrderStatus::Accepted,
            remote_order_id: Some(kairos_domain_types::RemoteOrderId::new("exchange-1").unwrap()),
            occurred_at_unix_nanos: 42.into(),
            reason: String::new(),
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        };
        audit.publish(&event).unwrap();
        audit.publish(&event).unwrap();
        let events = audit
            .query(&ExecutionAuditQuery {
                order_id: Some(kairos_domain_types::OrderId::new("order-1").unwrap()),
                ..Default::default()
            })
            .unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].remote_order_id.as_deref(), Some("exchange-1"));
    }
}
