CREATE TABLE IF NOT EXISTS execution_checkpoints (
    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation INTEGER NOT NULL,
    event_sequence INTEGER NOT NULL,
    payload BLOB NOT NULL,
    created_at_unix_nanos INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS execution_checkpoints_sequence
    ON execution_checkpoints(event_sequence);

CREATE TABLE IF NOT EXISTS execution_outbox (
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    event_kind TEXT NOT NULL,
    payload BLOB NOT NULL,
    created_at_unix_nanos INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS execution_outbox_id
    ON execution_outbox(outbox_id);

CREATE TABLE IF NOT EXISTS execution_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    status TEXT NOT NULL,
    remote_order_id TEXT,
    occurred_at_unix_nanos INTEGER NOT NULL,
    reason TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS intent_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL,
    status TEXT NOT NULL,
    order_ids TEXT NOT NULL,
    completed_quantity_mantissa INTEGER NOT NULL,
    occurred_at_unix_nanos INTEGER NOT NULL,
    reason TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE
);
