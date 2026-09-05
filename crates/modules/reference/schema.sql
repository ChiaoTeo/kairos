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
    scope_kind TEXT NOT NULL,
    scope_id TEXT,
    desired_state TEXT NOT NULL
        CHECK (desired_state IN ('enabled', 'disabled', 'paused', 'removed')),
    connection_id TEXT,
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
    ON reference_source_registry(provider_id, desired_state, source_id);

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
) VALUES (1, 10, 0, 0, 0);

-- v2 removes the derived Access views. Canonical provider facts can
-- rebuild every retained Reference row; these tables never owned history.
DROP TABLE IF EXISTS reference_execution_accesses_current;
DROP TABLE IF EXISTS reference_market_data_accesses_current;

CREATE TABLE IF NOT EXISTS reference_exchanges_current (
    exchange_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reference_exchanges_status_idx
    ON reference_exchanges_current(status, exchange_id);

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

-- v3 separates formal listing venues from independently addressable
-- execution/reporting facilities. The v2 tables above remain an explicit
-- compatibility records while consumers migrate.
CREATE TABLE IF NOT EXISTS reference_venues_current (
    venue_id TEXT PRIMARY KEY,
    venue_kind TEXT NOT NULL,
    mic TEXT,
    operating_mic TEXT,
    parent_venue_id TEXT,
    jurisdiction TEXT,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reference_venues_mic_idx
    ON reference_venues_current(mic, status, venue_id);
CREATE INDEX IF NOT EXISTS reference_venues_parent_idx
    ON reference_venues_current(parent_venue_id, venue_id);

CREATE TABLE IF NOT EXISTS reference_venue_listings_current (
    listing_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    listing_venue_id TEXT NOT NULL,
    market_segment_id TEXT,
    listing_symbol TEXT NOT NULL,
    listing_role TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_to_unix_nanos INTEGER,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reference_venue_listings_instrument_idx
    ON reference_venue_listings_current(instrument_id, status, listing_id);
CREATE INDEX IF NOT EXISTS reference_venue_listings_venue_symbol_idx
    ON reference_venue_listings_current(listing_venue_id, listing_symbol, status);

CREATE TABLE IF NOT EXISTS reference_venue_markets_current (
    market_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    execution_venue_id TEXT NOT NULL,
    origin_listing_id TEXT,
    market_segment_id TEXT,
    venue_symbol TEXT,
    status TEXT NOT NULL,
    effective_to_unix_nanos INTEGER,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reference_venue_markets_instrument_idx
    ON reference_venue_markets_current(instrument_id, status, market_id);
CREATE INDEX IF NOT EXISTS reference_venue_markets_venue_symbol_idx
    ON reference_venue_markets_current(execution_venue_id, venue_symbol, status);
CREATE INDEX IF NOT EXISTS reference_venue_markets_listing_idx
    ON reference_venue_markets_current(origin_listing_id, status, market_id);

CREATE TABLE IF NOT EXISTS reference_provider_catalog_memberships_current (
    source_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    provider_symbol TEXT,
    provider_product TEXT,
    status TEXT NOT NULL,
    effective_to_unix_nanos INTEGER,
    payload TEXT NOT NULL,
    PRIMARY KEY(source_id, instrument_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS reference_provider_catalog_memberships_instrument_idx
    ON reference_provider_catalog_memberships_current(instrument_id, status, source_id);

CREATE TABLE IF NOT EXISTS reference_venue_identifier_mappings_current (
    mapping_key TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_product TEXT NOT NULL,
    identifier_kind TEXT NOT NULL,
    identifier TEXT NOT NULL,
    venue_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(provider, provider_product, identifier_kind, identifier)
);
CREATE INDEX IF NOT EXISTS reference_venue_identifier_mappings_venue_idx
    ON reference_venue_identifier_mappings_current(venue_id, status, mapping_key);

CREATE TABLE IF NOT EXISTS reference_coverage_current (
    coverage_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    completeness TEXT NOT NULL,
    state TEXT NOT NULL,
    generation INTEGER,
    event_sequence INTEGER,
    last_attempt_unix_nanos INTEGER,
    last_success_unix_nanos INTEGER,
    stale_after_unix_nanos INTEGER,
    has_last_known_good INTEGER NOT NULL CHECK(has_last_known_good IN (0, 1)),
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reference_coverage_source_state_idx
    ON reference_coverage_current(source_id, state, coverage_id);

-- Source workers stage conclusion-changing coverage transitions here. The
-- Actor applies them with the catalog watermark, lifecycle event and outbox
-- publication in one transaction.
CREATE TABLE IF NOT EXISTS reference_coverage_pending_transition (
    source_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN (
        'not_configured', 'waiting', 'scanning', 'promoting', 'usable',
        'stale', 'retry_waiting', 'paused', 'unavailable'
    )),
    has_last_known_good INTEGER NOT NULL CHECK(has_last_known_good IN (0, 1)),
    last_attempt_unix_nanos INTEGER NOT NULL
) WITHOUT ROWID;

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

CREATE TABLE IF NOT EXISTS reference_provider_sync (
    provider TEXT PRIMARY KEY,
    cursor TEXT,
    updated_at_unix_nanos INTEGER NOT NULL
) WITHOUT ROWID;

-- Normalized pages contain canonical IDs, so unfinished scans must be
-- restarted when those identity rules change. This version is independent
-- from the public catalog schema and never invalidates committed records.
CREATE TABLE IF NOT EXISTS reference_provider_scan_format (
    provider TEXT PRIMARY KEY,
    version INTEGER NOT NULL
) WITHOUT ROWID;
