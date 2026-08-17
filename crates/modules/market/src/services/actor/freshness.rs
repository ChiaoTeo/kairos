use super::MarketActor;
use crate::domain::events::{MarketChange, MarketViewUpdate};
use crate::domain::freshness::freshness_status;

impl MarketActor {
    pub fn evaluate_freshness(&mut self, now_unix_nanos: u64, max_age_nanos: u64) {
        let mut changed = Vec::new();
        for freshness in self.freshness.values_mut() {
            let next_status = freshness_status(
                now_unix_nanos,
                freshness.last_received_time_unix_nanos.get(),
                max_age_nanos,
            );
            if freshness.status != next_status {
                freshness.status = next_status;
                self.event_sequence += 1;
                freshness.event_sequence = self.event_sequence;
                changed.push((self.event_sequence, freshness.clone()));
            }
        }
        for (sequence, freshness) in changed {
            if self.pending_changes.len() >= Self::MAX_PENDING_EVENTS {
                break;
            }
            self.pending_changes.push(MarketChange {
                sequence,
                event: None,
                view: Some(MarketViewUpdate::Freshness(freshness)),
            });
        }
    }
}
