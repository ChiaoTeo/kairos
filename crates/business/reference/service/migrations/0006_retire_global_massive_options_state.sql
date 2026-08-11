-- `massive-options` used to represent one unbounded global cursor and one
-- global last-known-good catalog. Scoped coverage stores state under
-- `massive-options:<UNDERLYING>` instead. The old snapshot is incompatible
-- with that new meaning and must never become a fallback after this upgrade.
DELETE FROM reference_provider_staging WHERE provider = 'massive-options';
DELETE FROM reference_provider_sync WHERE provider = 'massive-options';
