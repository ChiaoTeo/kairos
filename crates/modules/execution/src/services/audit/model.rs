use crate::domain::ExecutionOrderStatus;
use kairos_primitives::{OrderId, RemoteOrderId, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use crate::application::IntentAdmissionEvidence;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct IntentAdmissionAuditRecord {
    pub command_id: Option<String>,
    pub idempotency_key: String,
    pub intent_id: String,
    pub evidence: IntentAdmissionEvidence,
    pub admission_result: String,
    pub created_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAuditQuery {
    pub order_id: Option<OrderId>,
    pub remote_order_id: Option<RemoteOrderId>,
    pub status: Option<String>,
    pub since_unix_nanos: Option<UnixNanos>,
    pub until_unix_nanos: Option<UnixNanos>,
    pub limit: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAuditEvent {
    pub sequence: Sequence,
    pub order_id: OrderId,
    pub status: ExecutionOrderStatus,
    pub remote_order_id: Option<RemoteOrderId>,
    pub occurred_at_unix_nanos: UnixNanos,
    pub reason: String,
    #[serde(default)]
    pub attempt: Option<crate::domain::ExecutionAttempt>,
}
