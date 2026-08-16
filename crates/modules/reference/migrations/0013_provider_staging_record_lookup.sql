-- Promotion resolves the newest occurrence of each provider fact across
-- staged pages.  The table's primary key is ordered by page ordinal, which
-- cannot efficiently answer that lookup for a large scan.
CREATE INDEX IF NOT EXISTS reference_provider_staging_record_lookup_idx
    ON reference_provider_staging(provider, record_kind, record_id, ordinal DESC);
