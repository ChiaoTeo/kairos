CREATE TABLE IF NOT EXISTS reference_catalog (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_lifecycle (
    sequence INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL DEFAULT '',
    record_kind TEXT,
    record_id TEXT,
    market_id TEXT,
    exchange_id TEXT,
    event_time_unix_nanos INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_pending_publication (
    event_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_provider_sync (
    provider TEXT PRIMARY KEY,
    cursor TEXT,
    accumulated_catalog TEXT,
    last_good_catalog TEXT,
    updated_at_unix_nanos INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS reference_lifecycle_event_time_idx
    ON reference_lifecycle(event_time_unix_nanos);
CREATE INDEX IF NOT EXISTS reference_lifecycle_record_idx
    ON reference_lifecycle(record_kind, record_id);
CREATE INDEX IF NOT EXISTS reference_lifecycle_market_idx
    ON reference_lifecycle(market_id);
CREATE INDEX IF NOT EXISTS reference_lifecycle_type_idx
    ON reference_lifecycle(event_type);
