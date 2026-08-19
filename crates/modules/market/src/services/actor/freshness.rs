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

#[cfg(test)]
mod tests {
    use kairos_primitives::{Sequence, UnixNanos};

    use super::*;
    use crate::ObservationKind;
    use crate::domain::freshness::{DataFreshnessStatus, MarketFreshness};

    #[test]
    fn freshness_view_change_does_not_create_an_event_stream_gap() {
        let mut actor = MarketActor::new("market", 16, 16).unwrap();
        actor.event_sequence = Sequence::new(9);
        actor.freshness.insert(
            "aapl".into(),
            MarketFreshness {
                source_id: kairos_primitives::SourceId::new("source").unwrap(),
                scope: crate::ObservationScope::market("market:exchange:nasdaq:equity:AAPL")
                    .unwrap(),
                data_kind: ObservationKind::Quote,
                last_event_time_unix_nanos: UnixNanos::new(1),
                last_received_time_unix_nanos: UnixNanos::new(1),
                event_sequence: Sequence::new(9),
                status: DataFreshnessStatus::Current,
            },
        );

        actor.evaluate_freshness(100, 10);

        assert_eq!(actor.event_sequence.get(), 9);
        let change = actor.pending_changes.pop().unwrap();
        assert_eq!(change.sequence.get(), 9);
        assert!(change.event.is_none());
    }
}
