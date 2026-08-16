CREATE TABLE IF NOT EXISTS reference_outbox_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pending_count INTEGER NOT NULL CHECK (pending_count >= 0)
);

INSERT OR IGNORE INTO reference_outbox_state(id, pending_count)
SELECT 1, COUNT(*) FROM reference_pending_publication;
