use std::time::{SystemTime, UNIX_EPOCH};

use super::actor_task::MarketActorTask;

impl MarketActorTask {
    pub(super) fn run_maintenance(&mut self) {
        let now_nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .min(u128::from(u64::MAX)) as u64;
        let max_age_nanos = self.freshness_max_age.as_nanos().min(u128::from(u64::MAX)) as u64;
        self.application
            .evaluate_freshness(now_nanos, max_age_nanos);
    }
}
