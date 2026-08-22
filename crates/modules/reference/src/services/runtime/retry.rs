//! Retry policy for Reference source workflow runtime.

use kairos_primitives::time::UnixNanos;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct SourceRetryPolicy {
    base_seconds: u64,
    max_seconds: u64,
    max_exponent: u32,
}

impl Default for SourceRetryPolicy {
    fn default() -> Self {
        Self {
            base_seconds: 5,
            max_seconds: 300,
            max_exponent: 6,
        }
    }
}

impl SourceRetryPolicy {
    pub(super) fn backoff_seconds(self, consecutive_failures: u32) -> u64 {
        let exponent = consecutive_failures.min(self.max_exponent);
        self.base_seconds
            .saturating_mul(1u64 << exponent)
            .min(self.max_seconds)
    }
}

pub(super) fn retry_after_unix_nanos(backoff_seconds: u64) -> UnixNanos {
    let now = crate::services::time::unix_nanos().get();
    let backoff_nanos = backoff_seconds.saturating_mul(1_000_000_000);
    UnixNanos::from(now.saturating_add(backoff_nanos))
}
