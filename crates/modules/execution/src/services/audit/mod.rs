//! Concrete audit service selected by composition.
//!
//! Application does not define an audit port. The process directly uses this
//! lower-level service, whose variants are the production persistence modes.

mod memory;
mod model;
mod sqlx;

use crate::application::{ExecutionEvent, IntentEvent};

pub use memory::MemoryExecutionAudit;
pub use model::{ExecutionAuditEvent, ExecutionAuditQuery};
pub use sqlx::SqlxExecutionAudit;

pub enum ExecutionAudit {
    Memory(MemoryExecutionAudit),
    Sqlx(SqlxExecutionAudit),
}

impl ExecutionAudit {
    pub fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intents: &[IntentEvent],
    ) -> Result<(), String> {
        match self {
            Self::Memory(audit) => audit.publish_batch(events, intents),
            Self::Sqlx(audit) => audit.publish_batch(events, intents),
        }
    }

    pub fn query(
        &mut self,
        query: &ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, String> {
        match self {
            Self::Memory(audit) => audit.query(query),
            Self::Sqlx(audit) => audit.query(query),
        }
    }
}

impl From<MemoryExecutionAudit> for ExecutionAudit {
    fn from(audit: MemoryExecutionAudit) -> Self {
        Self::Memory(audit)
    }
}

impl From<SqlxExecutionAudit> for ExecutionAudit {
    fn from(audit: SqlxExecutionAudit) -> Self {
        Self::Sqlx(audit)
    }
}
