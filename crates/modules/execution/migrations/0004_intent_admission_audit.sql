CREATE TABLE IF NOT EXISTS intent_admission_audit (
    decision_id TEXT PRIMARY KEY,
    command_id TEXT,
    idempotency_key TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    source TEXT NOT NULL,
    outcome TEXT NOT NULL,
    original_intent_json TEXT NOT NULL,
    effective_intent_json TEXT NOT NULL,
    original_hash TEXT NOT NULL,
    effective_hash TEXT NOT NULL,
    admission_result TEXT NOT NULL,
    created_at_unix_nanos INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS intent_admission_audit_intent
    ON intent_admission_audit(intent_id);
