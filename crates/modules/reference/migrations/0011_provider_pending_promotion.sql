CREATE TABLE IF NOT EXISTS reference_provider_pending_promotion (
    provider TEXT PRIMARY KEY,
    operation TEXT NOT NULL CHECK(operation IN ('promote', 'delete'))
) WITHOUT ROWID;
