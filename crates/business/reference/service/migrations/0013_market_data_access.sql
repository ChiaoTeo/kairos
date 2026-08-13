-- Market-data provider addresses are independent from execution routes.
CREATE TABLE IF NOT EXISTS reference_market_data_accesses_current (
    access_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    product_family TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_to_unix_nanos INTEGER,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS reference_market_data_accesses_market_status_idx
    ON reference_market_data_accesses_current(market_id, status);

CREATE INDEX IF NOT EXISTS reference_market_data_accesses_provider_symbol_idx
    ON reference_market_data_accesses_current(provider_id, provider_symbol, product_family);
