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

CREATE INDEX IF NOT EXISTS reference_lifecycle_event_time_idx
    ON reference_lifecycle(event_time_unix_nanos);
CREATE INDEX IF NOT EXISTS reference_lifecycle_record_idx
    ON reference_lifecycle(record_kind, record_id);
CREATE INDEX IF NOT EXISTS reference_lifecycle_market_idx
    ON reference_lifecycle(market_id);
CREATE INDEX IF NOT EXISTS reference_lifecycle_type_idx
    ON reference_lifecycle(event_type);

CREATE TABLE IF NOT EXISTS reference_provider_control (
    provider TEXT PRIMARY KEY,
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    desired_state TEXT NOT NULL DEFAULT 'enabled'
        CHECK (desired_state IN ('enabled', 'disabled', 'paused', 'removed')),
    updated_at_unix_nanos INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_source_registry (
    source_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    provider_product TEXT,
    scope_kind TEXT NOT NULL,
    scope_id TEXT,
    desired_state TEXT NOT NULL
        CHECK (desired_state IN ('enabled', 'disabled', 'paused', 'removed')),
    credential_binding TEXT,
    sync_policy TEXT NOT NULL
        CHECK (sync_policy IN (
            'full_snapshot',
            'paged_snapshot',
            'scoped_snapshot',
            'incremental_delta',
            'manual_curated'
        )),
    payload TEXT NOT NULL,
    updated_at_unix_nanos INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS reference_source_registry_provider_idx
    ON reference_source_registry(provider_id, provider_product, desired_state, source_id);

CREATE TABLE IF NOT EXISTS reference_option_coverage (
    provider TEXT NOT NULL,
    underlying TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_at_unix_nanos INTEGER NOT NULL,
    PRIMARY KEY(provider, underlying)
);

CREATE INDEX IF NOT EXISTS reference_option_coverage_enabled_idx
    ON reference_option_coverage(provider, enabled, underlying);

CREATE TABLE IF NOT EXISTS reference_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    event_sequence INTEGER NOT NULL,
    committed_at_unix_nanos INTEGER NOT NULL
);

INSERT OR IGNORE INTO reference_meta(
    id,
    schema_version,
    generation,
    event_sequence,
    committed_at_unix_nanos
) VALUES (1, 4, 0, 0, 0);

-- v2 removes the derived Access projections. Canonical provider facts can
-- rebuild every retained Reference row; these tables never owned history.
DROP TABLE IF EXISTS reference_execution_accesses_current;
DROP TABLE IF EXISTS reference_market_data_accesses_current;

CREATE TABLE IF NOT EXISTS reference_entities_current (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reference_entities_type_status_idx
    ON reference_entities_current(entity_type, status, entity_id);

CREATE TABLE IF NOT EXISTS reference_assets_current (
    asset_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reference_assets_code_class_status_idx
    ON reference_assets_current(code, asset_class, status, asset_id);

CREATE TABLE IF NOT EXISTS reference_instruments_current (
    instrument_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    product_family TEXT,
    underlying_instrument_id TEXT,
    expiry_unix_nanos INTEGER,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS reference_instruments_symbol_idx
    ON reference_instruments_current(symbol, instrument_type);
CREATE INDEX IF NOT EXISTS reference_instruments_underlying_idx
    ON reference_instruments_current(underlying_instrument_id, expiry_unix_nanos);
CREATE INDEX IF NOT EXISTS reference_instruments_product_status_idx
    ON reference_instruments_current(product_family, status, instrument_id);

CREATE TABLE IF NOT EXISTS reference_listings_current (
    listing_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    exchange_id TEXT NOT NULL,
    exchange_symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_to_unix_nanos INTEGER,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS reference_listings_instrument_idx
    ON reference_listings_current(instrument_id, status);
CREATE INDEX IF NOT EXISTS reference_listings_exchange_symbol_idx
    ON reference_listings_current(exchange_id, exchange_symbol);

CREATE TABLE IF NOT EXISTS reference_markets_current (
    market_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    listing_id TEXT,
    exchange_id TEXT NOT NULL,
    instrument_kind TEXT NOT NULL,
    asset_type TEXT,
    underlying_instrument_id TEXT,
    venue_symbol TEXT,
    status TEXT NOT NULL,
    effective_to_unix_nanos INTEGER,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS reference_markets_instrument_idx
    ON reference_markets_current(instrument_id, status, market_id);
CREATE INDEX IF NOT EXISTS reference_markets_symbol_venue_idx
    ON reference_markets_current(venue_symbol, exchange_id, instrument_kind, status);
CREATE INDEX IF NOT EXISTS reference_markets_underlying_idx
    ON reference_markets_current(underlying_instrument_id, status, market_id);
CREATE INDEX IF NOT EXISTS reference_markets_exchange_idx
    ON reference_markets_current(exchange_id, status, market_id);
CREATE INDEX IF NOT EXISTS reference_markets_listing_idx
    ON reference_markets_current(listing_id, status, market_id);

CREATE TABLE IF NOT EXISTS reference_publication_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    published_sequence INTEGER NOT NULL
);

INSERT OR IGNORE INTO reference_publication_state(id, published_sequence)
VALUES (1, 0);

CREATE TABLE IF NOT EXISTS reference_publication_outbox (
    sequence INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    payload BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_provider_records (
    provider TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(provider, record_kind, record_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS reference_provider_records_identity_idx
    ON reference_provider_records(record_kind, record_id, provider);

CREATE TABLE IF NOT EXISTS reference_provider_staging (
    provider TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    record_kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(provider, ordinal, record_kind, record_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS reference_provider_staging_provider_idx
    ON reference_provider_staging(provider, ordinal, record_kind, record_id);
CREATE INDEX IF NOT EXISTS reference_provider_staging_record_lookup_idx
    ON reference_provider_staging(provider, record_kind, record_id, ordinal DESC);

CREATE TABLE IF NOT EXISTS reference_provider_pending_promotion (
    provider TEXT PRIMARY KEY,
    operation TEXT NOT NULL CHECK(operation IN ('promote', 'delete'))
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS reference_provider_sync (
    provider TEXT PRIMARY KEY,
    cursor TEXT,
    updated_at_unix_nanos INTEGER NOT NULL
) WITHOUT ROWID;

-- Normalized pages contain canonical IDs, so unfinished scans must be
-- restarted when those identity rules change. This version is independent
-- from the public catalog schema and never invalidates committed records.
CREATE TABLE IF NOT EXISTS reference_provider_projection_version (
    provider TEXT PRIMARY KEY,
    version INTEGER NOT NULL
) WITHOUT ROWID;

UPDATE reference_meta SET schema_version = 4 WHERE id = 1 AND schema_version < 4;
