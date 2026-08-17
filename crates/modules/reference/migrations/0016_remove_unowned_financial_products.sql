-- Financial products never had a production Reference source or business
-- consumer. Remove the unused model instead of retaining a speculative
-- ownership surface in the canonical catalog.
DELETE FROM reference_provider_records WHERE record_kind = 'financial_product';
DELETE FROM reference_provider_staging WHERE record_kind = 'financial_product';
DROP TABLE IF EXISTS reference_financial_products_current;
