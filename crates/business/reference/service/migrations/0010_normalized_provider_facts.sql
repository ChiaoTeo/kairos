CREATE TABLE IF NOT EXISTS reference_provider_records (
    provider TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(provider, record_kind, record_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS reference_provider_records_identity_idx
    ON reference_provider_records(record_kind, record_id, provider);

DROP INDEX reference_provider_staging_provider_idx;
ALTER TABLE reference_provider_staging RENAME TO reference_provider_staging_pages_legacy;

CREATE TABLE reference_provider_staging (
    provider TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    record_kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(provider, ordinal, record_kind, record_id)
) WITHOUT ROWID;

CREATE INDEX reference_provider_staging_provider_idx
    ON reference_provider_staging(provider, ordinal, record_kind, record_id);

INSERT OR REPLACE INTO reference_provider_staging(provider, ordinal, record_kind, record_id, payload)
SELECT provider, ordinal, 'entity', json_extract(value, '$.entity_id'), value
FROM reference_provider_staging_pages_legacy, json_each(payload, '$.entities');
INSERT OR REPLACE INTO reference_provider_staging(provider, ordinal, record_kind, record_id, payload)
SELECT provider, ordinal, 'asset', json_extract(value, '$.asset_id'), value
FROM reference_provider_staging_pages_legacy, json_each(payload, '$.assets');
INSERT OR REPLACE INTO reference_provider_staging(provider, ordinal, record_kind, record_id, payload)
SELECT provider, ordinal, 'instrument', json_extract(value, '$.instrument_id'), value
FROM reference_provider_staging_pages_legacy, json_each(payload, '$.instruments');
INSERT OR REPLACE INTO reference_provider_staging(provider, ordinal, record_kind, record_id, payload)
SELECT provider, ordinal, 'listing', json_extract(value, '$.listing_id'), value
FROM reference_provider_staging_pages_legacy, json_each(payload, '$.listings');
INSERT OR REPLACE INTO reference_provider_staging(provider, ordinal, record_kind, record_id, payload)
SELECT provider, ordinal, 'market', json_extract(value, '$.market_id'), value
FROM reference_provider_staging_pages_legacy, json_each(payload, '$.markets');
INSERT OR REPLACE INTO reference_provider_staging(provider, ordinal, record_kind, record_id, payload)
SELECT provider, ordinal, 'financial_product', json_extract(value, '$.product_id'), value
FROM reference_provider_staging_pages_legacy, json_each(payload, '$.financial_products');
INSERT OR REPLACE INTO reference_provider_staging(provider, ordinal, record_kind, record_id, payload)
SELECT provider, ordinal, 'execution_access', json_extract(value, '$.access_id'), value
FROM reference_provider_staging_pages_legacy, json_each(payload, '$.execution_accesses');

DROP TABLE reference_provider_staging_pages_legacy;

INSERT OR REPLACE INTO reference_provider_records(provider, record_kind, record_id, payload)
SELECT provider, 'entity', json_extract(value, '$.entity_id'), value
FROM reference_provider_sync, json_each(last_good_catalog, '$.entities')
WHERE last_good_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_records(provider, record_kind, record_id, payload)
SELECT provider, 'asset', json_extract(value, '$.asset_id'), value
FROM reference_provider_sync, json_each(last_good_catalog, '$.assets')
WHERE last_good_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_records(provider, record_kind, record_id, payload)
SELECT provider, 'instrument', json_extract(value, '$.instrument_id'), value
FROM reference_provider_sync, json_each(last_good_catalog, '$.instruments')
WHERE last_good_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_records(provider, record_kind, record_id, payload)
SELECT provider, 'listing', json_extract(value, '$.listing_id'), value
FROM reference_provider_sync, json_each(last_good_catalog, '$.listings')
WHERE last_good_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_records(provider, record_kind, record_id, payload)
SELECT provider, 'market', json_extract(value, '$.market_id'), value
FROM reference_provider_sync, json_each(last_good_catalog, '$.markets')
WHERE last_good_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_records(provider, record_kind, record_id, payload)
SELECT provider, 'financial_product', json_extract(value, '$.product_id'), value
FROM reference_provider_sync, json_each(last_good_catalog, '$.financial_products')
WHERE last_good_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_records(provider, record_kind, record_id, payload)
SELECT provider, 'execution_access', json_extract(value, '$.access_id'), value
FROM reference_provider_sync, json_each(last_good_catalog, '$.execution_accesses')
WHERE last_good_catalog IS NOT NULL;

UPDATE reference_provider_sync SET last_good_catalog = NULL;
