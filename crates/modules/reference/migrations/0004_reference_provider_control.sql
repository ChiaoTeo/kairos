CREATE TABLE IF NOT EXISTS reference_provider_control (
    provider TEXT PRIMARY KEY,
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    updated_at_unix_nanos INTEGER NOT NULL
);
