use kairos_primitives::execution::OrderId;
use kairos_primitives::integration::RemoteOrderId;
use kairos_primitives::time::{Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

use crate::domain::{ExecutionAttempt, ExecutionOrderStatus};

/// Business query over Execution's durable order audit.
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAuditQuery {
    pub order_id: Option<OrderId>,
    pub remote_order_id: Option<RemoteOrderId>,
    pub status: Option<String>,
    pub since_unix_nanos: Option<UnixNanos>,
    pub until_unix_nanos: Option<UnixNanos>,
    pub limit: Option<u32>,
}

/// Execution-owned order lifecycle fact returned by an audit query.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionAuditEvent {
    pub sequence: Sequence,
    pub order_id: OrderId,
    pub status: ExecutionOrderStatus,
    pub remote_order_id: Option<RemoteOrderId>,
    pub occurred_at_unix_nanos: UnixNanos,
    pub reason: String,
    #[serde(default)]
    pub attempt: Option<ExecutionAttempt>,
}
