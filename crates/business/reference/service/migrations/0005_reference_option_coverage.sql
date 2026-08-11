CREATE TABLE IF NOT EXISTS reference_option_coverage (
    provider TEXT NOT NULL,
    underlying TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_at_unix_nanos INTEGER NOT NULL,
    PRIMARY KEY(provider, underlying)
);

CREATE INDEX IF NOT EXISTS reference_option_coverage_enabled_idx
    ON reference_option_coverage(provider, enabled, underlying);
