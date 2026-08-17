use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub(crate) struct MarketProcessSettings {
    pub(crate) publication_interval: Duration,
    pub(crate) freshness_check_interval: Duration,
    pub(crate) freshness_max_age: Duration,
    pub(crate) reference_recovery_interval: Duration,
    pub(crate) shutdown_timeout: Duration,
    pub(crate) publication_queue_capacity: usize,
}

pub(super) fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}

pub(super) fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}
