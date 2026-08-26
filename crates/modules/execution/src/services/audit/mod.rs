//! Concrete audit service selected by composition.
//!
//! Application does not define an audit port. The process directly uses this
//! lower-level service, whose variants are the production persistence modes.

mod memory;
mod model;
mod sqlx;

pub use memory::MemoryExecutionAudit;
pub(crate) use model::IntentAdmissionAuditRecord;
pub use model::{ExecutionAuditEvent, ExecutionAuditQuery};
pub use sqlx::SqlxExecutionAudit;

use crate::application::{ExecutionEvent, IntentEvent};

pub enum ExecutionAudit {
    Memory(MemoryExecutionAudit),
    Sqlx(SqlxExecutionAudit),
}

impl ExecutionAudit {
    pub fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intents: &[IntentEvent],
        admissions: &[IntentAdmissionAuditRecord],
    ) -> Result<(), String> {
        match self {
            Self::Memory(audit) => audit.publish_batch(events, intents, admissions),
            Self::Sqlx(audit) => audit.publish_batch(events, intents, admissions),
        }
    }

    pub fn query(
        &mut self,
        query: &ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, String> {
        match self {
            Self::Memory(audit) => Ok(audit.query(query)),
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
