//! SQLx-backed execution audit persistence.

use std::path::Path;

use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use sqlx::{Row, SqlitePool};

use super::{ExecutionAuditEvent, ExecutionAuditQuery, IntentAdmissionAuditRecord};
use crate::application::{ExecutionEvent, IntentEvent};
use crate::domain::ExecutionOrderStatus;

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
        let run = operation(self.pool.clone());
        let result = if tokio::runtime::Handle::try_current().is_ok() {
            tokio::task::block_in_place(|| self.runtime.block_on(run))
        } else {
            self.runtime.block_on(run)
        };
        result.map_err(|error| error.to_string())
    }

    pub(crate) fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intents: &[IntentEvent],
        admissions: &[IntentAdmissionAuditRecord],
    ) -> Result<(), String> {
        if events.is_empty() && intents.is_empty() && admissions.is_empty() {
            return Ok(());
        }
        let events = events.to_vec();
        let intents = intents.to_vec();
        let admissions = admissions.to_vec();
        self.run(|pool| async move {
            let mut transaction = pool.begin().await?;
            for event in &events {
                let attempt_payload = event.attempt.as_ref().map(serde_json::to_string).transpose()
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                sqlx::query("INSERT OR IGNORE INTO execution_events(order_id,status,remote_order_id,occurred_at_unix_nanos,reason,event_key,attempt_payload) VALUES (?,?,?,?,?,?,?)")
                    .bind(event.order_id.as_str())
                    .bind(format!("{:?}", event.status).to_ascii_lowercase())
                    .bind(event.remote_order_id.as_ref().map(kairos_primitives::integration::RemoteOrderId::as_str))
                    .bind(event.occurred_at_unix_nanos.get() as i64)
                    .bind(&event.reason)
                    .bind(order_event_key(event))
                    .bind(attempt_payload)
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
            for record in &admissions {
                let original = serde_json::to_string(&record.evidence.original_intent)
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                let effective = serde_json::to_string(&record.evidence.effective_intent)
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                let result = sqlx::query(
                    "INSERT INTO intent_admission_audit (decision_id,command_id,idempotency_key,intent_id,source,outcome,original_intent_json,effective_intent_json,original_hash,effective_hash,admission_result,created_at_unix_nanos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(decision_id) DO UPDATE SET admission_result=excluded.admission_result WHERE intent_admission_audit.command_id IS excluded.command_id AND intent_admission_audit.idempotency_key=excluded.idempotency_key AND intent_admission_audit.intent_id=excluded.intent_id AND intent_admission_audit.source=excluded.source AND intent_admission_audit.outcome=excluded.outcome AND intent_admission_audit.original_intent_json=excluded.original_intent_json AND intent_admission_audit.effective_intent_json=excluded.effective_intent_json AND intent_admission_audit.original_hash=excluded.original_hash AND intent_admission_audit.effective_hash=excluded.effective_hash",
                )
                .bind(&record.evidence.decision_id)
                .bind(&record.command_id)
                .bind(&record.idempotency_key)
                .bind(&record.intent_id)
                .bind(&record.evidence.source)
                .bind(&record.evidence.outcome)
                .bind(original)
                .bind(effective)
                .bind(&record.evidence.original_hash)
                .bind(&record.evidence.effective_hash)
                .bind(&record.admission_result)
                .bind(record.created_at_unix_nanos as i64)
                .execute(&mut *transaction)
                .await?;
                if result.rows_affected() != 1 {
                    return Err(sqlx::Error::Protocol(
                        "Decision admission evidence was reused with different facts".into(),
                    ));
                }
            }
            transaction.commit().await
        })
    }

    pub fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        self.publish_batch(std::slice::from_ref(event), &[], &[])
    }

    pub fn publish_intent(&mut self, event: &IntentEvent) -> Result<(), String> {
        self.publish_batch(&[], std::slice::from_ref(event), &[])
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
            let rows = sqlx::query("SELECT sequence,order_id,status,remote_order_id,occurred_at_unix_nanos,reason,attempt_payload FROM execution_events WHERE (? IS NULL OR order_id = ?) AND (? IS NULL OR remote_order_id = ?) AND (? IS NULL OR lower(status) = lower(?)) AND (? IS NULL OR occurred_at_unix_nanos >= ?) AND (? IS NULL OR occurred_at_unix_nanos <= ?) ORDER BY sequence ASC LIMIT ?")
                .bind(&order_id).bind(&order_id)
                .bind(&remote_order_id).bind(&remote_order_id)
                .bind(&status).bind(&status)
                .bind(since).bind(since).bind(until).bind(until).bind(limit)
                .fetch_all(&pool)
                .await?;
            rows.into_iter()
                .map(|row| {
                    let order_id = kairos_primitives::execution::OrderId::new(
                        row.try_get::<String, _>("order_id")?,
                    )
                    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                    let remote_order_id = row
                        .try_get::<Option<String>, _>("remote_order_id")?
                        .map(kairos_primitives::integration::RemoteOrderId::new)
                        .transpose()
                        .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
                    Ok(ExecutionAuditEvent {
                        sequence: (row.try_get::<i64, _>("sequence")? as u64).into(),
                        order_id,
                        status: audit_status(&row.try_get::<String, _>("status")?),
                        remote_order_id,
                        occurred_at_unix_nanos: (row
                            .try_get::<i64, _>("occurred_at_unix_nanos")?
                            as u64)
                            .into(),
                        reason: row.try_get("reason")?,
                        attempt: row
                            .try_get::<Option<String>, _>("attempt_payload")?
                            .map(|payload| serde_json::from_str(&payload))
                            .transpose()
                            .map_err(|error| sqlx::Error::Decode(Box::new(error)))?,
                    })
                })
                .collect()
        })
    }
}

fn runtime() -> Result<tokio::runtime::Runtime, String> {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|error| error.to_string())
}

fn audit_status(value: &str) -> ExecutionOrderStatus {
    match value.to_ascii_lowercase().replace(['-', '_'], "").as_str() {
        "pending" => ExecutionOrderStatus::Pending,
        "submitting" => ExecutionOrderStatus::Submitting,
        "accepted" => ExecutionOrderStatus::Accepted,
        "partiallyfilled" => ExecutionOrderStatus::PartiallyFilled,
        "filled" => ExecutionOrderStatus::Filled,
        "cancelrequested" => ExecutionOrderStatus::CancelRequested,
        "canceled" => ExecutionOrderStatus::Canceled,
        "rejected" => ExecutionOrderStatus::Rejected,
        "expired" => ExecutionOrderStatus::Expired,
        "failed" => ExecutionOrderStatus::Failed,
        _ => ExecutionOrderStatus::Unknown,
    }
}

fn order_event_key(event: &ExecutionEvent) -> String {
    format!(
        "{}|{:?}|{}|{}|{}",
        event.order_id,
        event.status,
        event
            .remote_order_id
            .as_ref()
            .map(kairos_primitives::integration::RemoteOrderId::as_str)
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
    use crate::application::{
        ExecuteStrategyIntent, ExecutionAuditQuery, ExecutionEvent, IntentAdmissionEvidence,
    };
    use crate::domain::ExecutionOrderStatus;
    use crate::services::audit::IntentAdmissionAuditRecord;

    #[test]
    fn sqlx_audit_is_idempotent_and_queryable() {
        let directory = tempfile::tempdir().unwrap();
        let mut audit = SqlxExecutionAudit::new(directory.path().join("audit.sqlite")).unwrap();
        let event = ExecutionEvent {
            order_id: kairos_primitives::execution::OrderId::new("order-1").unwrap(),
            intent_id: None,
            plan_id: None,
            leg_id: None,
            status: ExecutionOrderStatus::Accepted,
            remote_order_id: Some(
                kairos_primitives::integration::RemoteOrderId::new("exchange-1").unwrap(),
            ),
            occurred_at_unix_nanos: 42.into(),
            reason: String::new(),
            fill_id: None,
            filled_quantity: None,
            attempt: None,
        };
        audit.publish(&event).unwrap();
        audit.publish(&event).unwrap();
        let events = audit
            .query(&ExecutionAuditQuery {
                order_id: Some(kairos_primitives::execution::OrderId::new("order-1").unwrap()),
                ..Default::default()
            })
            .unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].remote_order_id.as_deref(), Some("exchange-1"));
    }

    #[test]
    fn sqlx_audit_preserves_every_order_lifecycle_exactly() {
        let directory = tempfile::tempdir().unwrap();
        let mut audit = SqlxExecutionAudit::new(directory.path().join("audit.sqlite")).unwrap();
        let statuses = [
            ExecutionOrderStatus::Pending,
            ExecutionOrderStatus::Submitting,
            ExecutionOrderStatus::Accepted,
            ExecutionOrderStatus::PartiallyFilled,
            ExecutionOrderStatus::Filled,
            ExecutionOrderStatus::CancelRequested,
            ExecutionOrderStatus::Canceled,
            ExecutionOrderStatus::Rejected,
            ExecutionOrderStatus::Expired,
            ExecutionOrderStatus::Unknown,
            ExecutionOrderStatus::Failed,
        ];
        for (index, status) in statuses.iter().copied().enumerate() {
            audit
                .publish(&ExecutionEvent {
                    order_id: kairos_primitives::execution::OrderId::new(format!("order-{index}"))
                        .unwrap(),
                    intent_id: None,
                    plan_id: None,
                    leg_id: None,
                    status,
                    remote_order_id: None,
                    occurred_at_unix_nanos: (index as u64 + 1).into(),
                    reason: String::new(),
                    fill_id: None,
                    filled_quantity: None,
                    attempt: None,
                })
                .unwrap();
        }

        let restored = audit.query(&ExecutionAuditQuery::default()).unwrap();
        assert_eq!(
            restored
                .iter()
                .map(|event| event.status)
                .collect::<Vec<_>>(),
            statuses
        );
    }

    #[test]
    fn admission_audit_updates_only_the_result_for_identical_evidence() {
        let directory = tempfile::tempdir().unwrap();
        let mut audit = SqlxExecutionAudit::new(directory.path().join("audit.sqlite")).unwrap();
        let intent = ExecuteStrategyIntent::test_fixture();
        let evidence = IntentAdmissionEvidence {
            source: "decision_agent".into(),
            decision_id: "decision-1".into(),
            outcome: "approved".into(),
            original_intent: intent.clone(),
            effective_intent: intent,
            original_hash: "a".repeat(64),
            effective_hash: "a".repeat(64),
        };
        let mut record = IntentAdmissionAuditRecord {
            command_id: Some("command-1".into()),
            idempotency_key: "idempotency-1".into(),
            intent_id: "intent:default".into(),
            evidence,
            admission_result: "accepted".into(),
            created_at_unix_nanos: 42,
        };

        audit.publish_batch(&[], &[], &[record.clone()]).unwrap();
        record.admission_result = "duplicate".into();
        audit.publish_batch(&[], &[], &[record.clone()]).unwrap();
        let result = audit
            .run(|pool| async move {
                sqlx::query_scalar::<_, String>(
                    "SELECT admission_result FROM intent_admission_audit WHERE decision_id = ?",
                )
                .bind("decision-1")
                .fetch_one(&pool)
                .await
            })
            .unwrap();
        assert_eq!(result, "duplicate");

        record.evidence.effective_hash = "b".repeat(64);
        let error = audit.publish_batch(&[], &[], &[record]).unwrap_err();
        assert!(error.contains("different facts"));
    }
}
