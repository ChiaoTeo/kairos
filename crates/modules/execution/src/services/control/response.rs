//! Control-plane response encoding.
//!
//! The process selects a typed outcome; this transport adapter owns the JSON
//! wire shape and status code returned over HTTP.

use serde::Serialize;
use serde_json::{json, Value};

pub(crate) struct ControlResponse {
    status: u16,
    payload: Value,
}

impl ControlResponse {
    pub(crate) fn into_wire(self) -> (u16, Value) {
        (self.status, self.payload)
    }

    pub(crate) fn health<T: Serialize>(
        status: &str,
        writer_recovery_ready: bool,
        routes: &T,
    ) -> Result<Self, serde_json::Error> {
        Ok(Self {
            status: 200,
            payload: json!({
                "status": status,
                "pid": std::process::id(),
                "writer_recovery_ready": writer_recovery_ready,
                "dependencies": { "order_event_routes": serde_json::to_value(routes)? }
            }),
        })
    }

    pub(crate) fn event_time(event_time_unix_nanos: u64) -> Self {
        Self {
            status: 200,
            payload: json!({"event_time_unix_nanos": event_time_unix_nanos}),
        }
    }

    pub(crate) fn intent_accepted(intent_id: &str, duplicate: bool) -> Self {
        Self {
            status: 202,
            payload: json!({
                "status": if duplicate { "duplicate" } else { "accepted" },
                "command_id": intent_id,
                "intent_id": intent_id,
            }),
        }
    }

    pub(crate) fn accepted_order(order_id: &str) -> Self {
        Self {
            status: 202,
            payload: json!({"status":"accepted", "order_id":order_id}),
        }
    }

    pub(crate) fn accepted_reconciliation(changed: usize) -> Self {
        Self {
            status: 202,
            payload: json!({"status":"accepted", "changed":changed}),
        }
    }

    pub(crate) fn serialized<T: Serialize>(
        status: u16,
        value: &T,
    ) -> Result<Self, serde_json::Error> {
        Ok(Self {
            status,
            payload: serde_json::to_value(value)?,
        })
    }

    pub(crate) fn fills<T: Serialize>(fills: &T) -> Result<Self, serde_json::Error> {
        Ok(Self {
            status: 200,
            payload: json!({"fills":serde_json::to_value(fills)?}),
        })
    }

    pub(crate) fn routes<T: Serialize>(routes: &T) -> Result<Self, serde_json::Error> {
        Ok(Self {
            status: 200,
            payload: json!({"routes":serde_json::to_value(routes)?}),
        })
    }

    pub(crate) fn intent_result<T: Serialize>(
        status: &str,
        result: &T,
    ) -> Result<Self, serde_json::Error> {
        Ok(Self {
            status: 202,
            payload: json!({
                "schema_version":1,
                "status":status,
                "result":serde_json::to_value(result)?,
            }),
        })
    }

    pub(crate) fn error(status: u16, message: impl ToString) -> Self {
        Self {
            status,
            payload: json!({"error":message.to_string()}),
        }
    }

    pub(crate) fn intent_error(code: &str, message: impl ToString) -> Self {
        Self {
            status: 422,
            payload: json!({
                "schema_version":1,
                "status":"rejected",
                "error":{
                    "code":code,
                    "message":message.to_string(),
                    "retryable":false,
                },
            }),
        }
    }

    pub(crate) fn stopping() -> Self {
        Self {
            status: 202,
            payload: json!({"status":"stopping"}),
        }
    }
}
