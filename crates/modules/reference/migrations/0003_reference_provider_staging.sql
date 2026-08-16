CREATE TABLE IF NOT EXISTS reference_provider_staging (
    provider TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(provider, ordinal)
);

CREATE INDEX IF NOT EXISTS reference_provider_staging_provider_idx
    ON reference_provider_staging(provider, ordinal);
