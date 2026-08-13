CREATE TABLE IF NOT EXISTS reference_publication_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    published_sequence INTEGER NOT NULL
);

INSERT OR IGNORE INTO reference_publication_state(id, published_sequence)
SELECT 1,
       COALESCE(
           (SELECT CAST(substr(MIN(event_id), 11) AS INTEGER) - 1
              FROM reference_pending_publication),
           (SELECT MAX(sequence) FROM reference_lifecycle),
           0
       );

DROP TABLE IF EXISTS reference_pending_publication;
DROP TABLE IF EXISTS reference_outbox_state;
