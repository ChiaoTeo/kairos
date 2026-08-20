use std::time::{SystemTime, UNIX_EPOCH};

use kairos_primitives::time::UnixNanos;

/// Reads wall-clock time at the service boundary. Domain reconciliation
/// receives the timestamp explicitly so it remains deterministic and replayable.
pub(crate) fn unix_nanos() -> UnixNanos {
    UnixNanos::from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64,
    )
}
