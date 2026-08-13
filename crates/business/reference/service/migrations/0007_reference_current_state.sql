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
) VALUES (1, 1, 0, 0, 0);

CREATE TABLE IF NOT EXISTS reference_entities_current (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_assets_current (
    asset_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL
);

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
    source_id TEXT,
    market_key TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    exchange_id TEXT NOT NULL,
    market_type TEXT NOT NULL,
    asset_type TEXT,
    underlying_instrument_id TEXT,
    source_symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_to_unix_nanos INTEGER,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS reference_markets_instrument_idx
    ON reference_markets_current(instrument_id, status, market_id);
CREATE INDEX IF NOT EXISTS reference_markets_provider_symbol_idx
    ON reference_markets_current(source_id, source_symbol, market_type);
CREATE INDEX IF NOT EXISTS reference_markets_underlying_idx
    ON reference_markets_current(underlying_instrument_id, status, market_id);
CREATE INDEX IF NOT EXISTS reference_markets_exchange_idx
    ON reference_markets_current(exchange_id, status, market_id);

CREATE TABLE IF NOT EXISTS reference_financial_products_current (
    product_id TEXT PRIMARY KEY,
    provider_id TEXT,
    provider_product_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    product_type TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_to_unix_nanos INTEGER,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS reference_financial_products_provider_idx
    ON reference_financial_products_current(provider_id, provider_product_id);

CREATE TABLE IF NOT EXISTS reference_execution_accesses_current (
    access_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    product_family TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_to_unix_nanos INTEGER,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS reference_execution_accesses_market_idx
    ON reference_execution_accesses_current(market_id, status);
CREATE INDEX IF NOT EXISTS reference_execution_accesses_provider_idx
    ON reference_execution_accesses_current(provider_id, provider_symbol, product_family);
