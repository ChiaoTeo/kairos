use crate::domain::IntentAdmissionEvidence;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct IntentAdmissionAuditRecord {
    pub command_id: Option<String>,
    pub idempotency_key: String,
    pub intent_id: String,
    pub evidence: IntentAdmissionEvidence,
    pub admission_result: String,
    pub created_at_unix_nanos: u64,
}
