use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::IntegrationError;

const MAX_ABSOLUTE_OFFSET_MILLIS: i64 = 300_000;
const MAX_SAMPLE_AGE: Duration = Duration::from_secs(300);

/// A bounded midpoint estimate of a provider clock. Provider services own
/// when and how samples are obtained; this value only validates and applies
/// the estimate.
#[derive(Clone, Debug, Default)]
pub(crate) struct ServerClock {
    offset_millis: i64,
    sampled_at: Option<Instant>,
}

impl ServerClock {
    pub(crate) fn health(&self) -> crate::ProviderClockHealth {
        let sample_age_millis = self
            .sampled_at
            .map(|sampled_at| u64::try_from(sampled_at.elapsed().as_millis()).unwrap_or(u64::MAX));
        crate::ProviderClockHealth {
            sampled: self.sampled_at.is_some(),
            fresh: self.is_fresh(),
            offset_millis: self.sampled_at.map(|_| self.offset_millis),
            sample_age_millis,
        }
    }

    pub(crate) fn observe(
        &mut self,
        provider_unix_millis: u64,
        request_started_unix_millis: u64,
        response_received_unix_millis: u64,
    ) -> Result<(), IntegrationError> {
        if response_received_unix_millis < request_started_unix_millis {
            return Err(clock_error("local clock moved backwards during sampling"));
        }
        let midpoint = request_started_unix_millis
            .saturating_add((response_received_unix_millis - request_started_unix_millis) / 2);
        let offset = i128::from(provider_unix_millis) - i128::from(midpoint);
        let offset = i64::try_from(offset)
            .map_err(|_| clock_error("provider clock offset cannot be represented"))?;
        if offset.unsigned_abs() > MAX_ABSOLUTE_OFFSET_MILLIS as u64 {
            return Err(clock_error(format!(
                "provider clock offset {offset}ms exceeds the 300000ms safety bound"
            )));
        }
        self.offset_millis = offset;
        self.sampled_at = Some(Instant::now());
        Ok(())
    }

    pub(crate) fn adjusted_unix_millis(&self) -> Result<u64, IntegrationError> {
        let sampled_at = self
            .sampled_at
            .ok_or_else(|| clock_error("provider clock has not been sampled"))?;
        if sampled_at.elapsed() > MAX_SAMPLE_AGE {
            return Err(clock_error("provider clock sample is stale"));
        }
        let local = unix_millis()?;
        if self.offset_millis >= 0 {
            local
                .checked_add(self.offset_millis as u64)
                .ok_or_else(|| clock_error("adjusted provider timestamp overflowed"))
        } else {
            local
                .checked_sub(self.offset_millis.unsigned_abs())
                .ok_or_else(|| clock_error("adjusted provider timestamp underflowed"))
        }
    }

    pub(crate) fn is_fresh(&self) -> bool {
        self.sampled_at
            .is_some_and(|sampled_at| sampled_at.elapsed() <= MAX_SAMPLE_AGE)
    }

    #[cfg(test)]
    pub(crate) fn offset_millis(&self) -> i64 {
        self.offset_millis
    }
}

pub(crate) fn unix_millis() -> Result<u64, IntegrationError> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| clock_error("system clock is before the Unix epoch"))?
        .as_millis()
        .try_into()
        .map_err(|_| clock_error("system clock cannot be represented in milliseconds"))
}

fn clock_error(message: impl Into<String>) -> IntegrationError {
    IntegrationError::Unavailable(format!("provider clock unavailable: {}", message.into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn midpoint_sample_estimates_and_applies_bounded_offset() {
        let mut clock = ServerClock::default();
        clock.observe(1_060, 1_000, 1_020).unwrap();
        assert_eq!(clock.offset_millis(), 50);
        assert!(clock.is_fresh());
        assert!(clock.adjusted_unix_millis().is_ok());
        assert_eq!(clock.health().offset_millis, Some(50));
    }

    #[test]
    fn rejects_clock_regression_and_extreme_offsets() {
        let mut clock = ServerClock::default();
        assert!(clock.observe(1_000, 1_020, 1_000).is_err());
        assert!(clock.observe(1_000_000, 1_000, 1_010).is_err());
        assert!(!clock.is_fresh());
    }
}
