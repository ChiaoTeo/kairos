use super::DataFreshnessStatus;

pub fn freshness_status(
    now_unix_nanos: u64,
    last_received_time_unix_nanos: u64,
    max_age_nanos: u64,
) -> DataFreshnessStatus {
    if now_unix_nanos.saturating_sub(last_received_time_unix_nanos) > max_age_nanos {
        DataFreshnessStatus::Stale
    } else {
        DataFreshnessStatus::Current
    }
}

#[cfg(test)]
mod tests {
    use super::freshness_status;
    use crate::domain::freshness::DataFreshnessStatus;

    #[test]
    fn age_equal_to_threshold_is_still_current() {
        assert_eq!(freshness_status(20, 10, 10), DataFreshnessStatus::Current);
        assert_eq!(freshness_status(21, 10, 10), DataFreshnessStatus::Stale);
    }

    #[test]
    fn future_receive_time_does_not_underflow() {
        assert_eq!(freshness_status(10, 20, 0), DataFreshnessStatus::Current);
    }
}
