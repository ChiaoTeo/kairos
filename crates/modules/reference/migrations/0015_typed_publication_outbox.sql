CREATE TABLE IF NOT EXISTS reference_publication_outbox (
    sequence INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    payload BLOB NOT NULL
);

-- Pre-migration events were reconstructed from mutable current rows and
-- cannot be made revision-faithful retroactively. The typed outbox starts at
-- the current durable lifecycle watermark.
UPDATE reference_publication_state
SET published_sequence = MAX(
    published_sequence,
    COALESCE((SELECT MAX(sequence) FROM reference_lifecycle), 0)
)
WHERE id = 1;
