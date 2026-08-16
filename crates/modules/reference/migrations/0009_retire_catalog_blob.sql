INSERT OR IGNORE INTO reference_entities_current(entity_id,entity_type,status,payload)
SELECT key, json_extract(value,'$.entity_type'), json_extract(value,'$.status'), value
FROM reference_catalog, json_each(reference_catalog.payload,'$.entities') WHERE reference_catalog.id = 1;

INSERT OR IGNORE INTO reference_assets_current(asset_id,code,asset_class,status,payload)
SELECT key, json_extract(value,'$.code'), json_extract(value,'$.asset_class'), json_extract(value,'$.status'), value
FROM reference_catalog, json_each(reference_catalog.payload,'$.assets') WHERE reference_catalog.id = 1;

INSERT OR IGNORE INTO reference_instruments_current(instrument_id,symbol,instrument_type,product_family,underlying_instrument_id,expiry_unix_nanos,status,payload)
SELECT key, json_extract(value,'$.symbol'), json_extract(value,'$.instrument_type'),
       json_extract(value,'$.product_family'), json_extract(value,'$.underlying_instrument_id'),
       json_extract(value,'$.expiry_unix_nanos'), json_extract(value,'$.status'), value
FROM reference_catalog, json_each(reference_catalog.payload,'$.instruments') WHERE reference_catalog.id = 1;

INSERT OR IGNORE INTO reference_listings_current(listing_id,instrument_id,exchange_id,exchange_symbol,status,effective_to_unix_nanos,payload)
SELECT key, json_extract(value,'$.instrument_id'), json_extract(value,'$.exchange_id'),
       json_extract(value,'$.exchange_symbol'), json_extract(value,'$.status'),
       json_extract(value,'$.effective_to_unix_nanos'), value
FROM reference_catalog, json_each(reference_catalog.payload,'$.listings') WHERE reference_catalog.id = 1;

INSERT OR IGNORE INTO reference_markets_current(market_id,source_id,market_key,instrument_id,listing_id,exchange_id,market_type,asset_type,underlying_instrument_id,source_symbol,status,effective_to_unix_nanos,payload)
SELECT key, json_extract(value,'$.source_id'), json_extract(value,'$.market_key'),
       json_extract(value,'$.instrument_id'), json_extract(value,'$.listing_id'),
       json_extract(value,'$.exchange_id'), json_extract(value,'$.market_type'),
       json_extract(value,'$.asset_type'), json_extract(value,'$.underlying_instrument_id'),
       json_extract(value,'$.source_symbol'), json_extract(value,'$.status'),
       json_extract(value,'$.effective_to_unix_nanos'), value
FROM reference_catalog, json_each(reference_catalog.payload,'$.markets') WHERE reference_catalog.id = 1;

INSERT OR IGNORE INTO reference_financial_products_current(product_id,provider_id,provider_product_id,asset_id,product_type,status,effective_to_unix_nanos,payload)
SELECT key, json_extract(value,'$.provider_id'), json_extract(value,'$.provider_product_id'),
       json_extract(value,'$.asset_id'), json_extract(value,'$.product_type'),
       json_extract(value,'$.status'), json_extract(value,'$.effective_to_unix_nanos'), value
FROM reference_catalog, json_each(reference_catalog.payload,'$.financial_products') WHERE reference_catalog.id = 1;

INSERT OR IGNORE INTO reference_execution_accesses_current(access_id,market_id,provider_id,product_family,provider_symbol,status,effective_to_unix_nanos,payload)
SELECT key, json_extract(value,'$.market_id'), json_extract(value,'$.provider_id'),
       json_extract(value,'$.product_family'), json_extract(value,'$.provider_symbol'),
       json_extract(value,'$.status'), json_extract(value,'$.effective_to_unix_nanos'), value
FROM reference_catalog, json_each(reference_catalog.payload,'$.execution_accesses') WHERE reference_catalog.id = 1;

UPDATE reference_meta
SET generation = COALESCE((SELECT json_extract(payload,'$.generation') FROM reference_catalog WHERE id = 1), generation),
    event_sequence = COALESCE((SELECT json_extract(payload,'$.event_sequence') FROM reference_catalog WHERE id = 1), event_sequence)
WHERE id = 1;

DROP TABLE reference_catalog;
