//! Workspace-owned allocation of one producer-process incarnation.

use std::num::NonZeroU64;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static ALLOCATION_COUNTER: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ProducerIncarnation(NonZeroU64);

impl ProducerIncarnation {
    /// Allocate an opaque value for one concrete publisher lifetime.
    ///
    /// The value is identity, not ordering. Consumers compare it for equality
    /// and must not infer restart order from its magnitude.
    pub fn allocate() -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64;
        let counter = ALLOCATION_COUNTER.fetch_add(1, Ordering::Relaxed);
        let value = nanos ^ (u64::from(std::process::id()) << 32) ^ counter.rotate_left(17);
        Self(NonZeroU64::new(value).unwrap_or(NonZeroU64::MIN))
    }

    pub fn get(self) -> u64 {
        self.0.get()
    }
}

#[cfg(test)]
mod tests {
    use super::ProducerIncarnation;

    #[test]
    fn allocations_are_positive_and_distinct() {
        let first = ProducerIncarnation::allocate();
        let second = ProducerIncarnation::allocate();
        assert_ne!(first, second);
        assert!(first.get() > 0);
    }
}
