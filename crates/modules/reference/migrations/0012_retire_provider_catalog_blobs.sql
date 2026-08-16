INSERT OR REPLACE INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload)
SELECT provider,-1,'entity',json_extract(value,'$.entity_id'),value
FROM reference_provider_sync,json_each(accumulated_catalog,'$.entities') WHERE accumulated_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload)
SELECT provider,-1,'asset',json_extract(value,'$.asset_id'),value
FROM reference_provider_sync,json_each(accumulated_catalog,'$.assets') WHERE accumulated_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload)
SELECT provider,-1,'instrument',json_extract(value,'$.instrument_id'),value
FROM reference_provider_sync,json_each(accumulated_catalog,'$.instruments') WHERE accumulated_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload)
SELECT provider,-1,'listing',json_extract(value,'$.listing_id'),value
FROM reference_provider_sync,json_each(accumulated_catalog,'$.listings') WHERE accumulated_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload)
SELECT provider,-1,'market',json_extract(value,'$.market_id'),value
FROM reference_provider_sync,json_each(accumulated_catalog,'$.markets') WHERE accumulated_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload)
SELECT provider,-1,'financial_product',json_extract(value,'$.product_id'),value
FROM reference_provider_sync,json_each(accumulated_catalog,'$.financial_products') WHERE accumulated_catalog IS NOT NULL;
INSERT OR REPLACE INTO reference_provider_staging(provider,ordinal,record_kind,record_id,payload)
SELECT provider,-1,'execution_access',json_extract(value,'$.access_id'),value
FROM reference_provider_sync,json_each(accumulated_catalog,'$.execution_accesses') WHERE accumulated_catalog IS NOT NULL;

CREATE TABLE reference_provider_sync_normalized (
    provider TEXT PRIMARY KEY,
    cursor TEXT,
    updated_at_unix_nanos INTEGER NOT NULL
) WITHOUT ROWID;

INSERT INTO reference_provider_sync_normalized(provider,cursor,updated_at_unix_nanos)
SELECT provider,cursor,updated_at_unix_nanos FROM reference_provider_sync;

DROP TABLE reference_provider_sync;
ALTER TABLE reference_provider_sync_normalized RENAME TO reference_provider_sync;
