use super::super::MarketActor;
use crate::domain::events::{MarketChange, MarketEvent, MarketViewUpdate};
use crate::domain::freshness::{DataFreshnessStatus, FeedStatus, MarketFreshness};
use crate::domain::observation::MarketObservation;
use crate::domain::subscription::selector_matches_observation;

impl MarketActor {
    pub fn apply_observation(&mut self, observation: MarketObservation) -> Result<u64, String> {
        observation.validate()?;
        if !self.observation_is_selected(&observation) {
            return Ok(self.event_sequence.get());
        }
        if self.pending_changes.len() >= Self::MAX_PENDING_EVENTS {
            return Err(format!(
                "market event backlog exceeded limit {}",
                Self::MAX_PENDING_EVENTS
            ));
        }
        let view = observation
            .view_key()
            .map_err(|error| format!("invalid market observation view: {error}"))?
            .as_str();
        let view_is_newer = self.views.get(&view).is_none_or(|current| {
            current.observed_at_unix_nanos() <= observation.observed_at_unix_nanos()
        });
        if view_is_newer {
            self.views.insert(view, observation.clone());
        }
        let freshness_key = observation
            .view_key()
            .map_err(|error| format!("invalid market freshness view: {error}"))?
            .as_str();
        let next_sequence = self.event_sequence.get().saturating_add(1);
        self.freshness.insert(
            freshness_key,
            MarketFreshness {
                source_id: observation.source_id().to_owned(),
                market_id: kairos_primitives::MarketId::new(observation.market_id().to_owned())
                    .expect("validated market observation market id"),
                data_kind: observation.kind(),
                last_event_time_unix_nanos: observation.observed_at_unix_nanos(),
                last_received_time_unix_nanos: kairos_primitives::UnixNanos::new(
                    super::super::now_unix_nanos(),
                ),
                event_sequence: kairos_primitives::Sequence::new(next_sequence),
                status: DataFreshnessStatus::Current,
            },
        );
        if self.feed_status == FeedStatus::WarmingUp {
            self.feed_status = FeedStatus::Ready;
        }
        self.event_sequence += 1;
        self.pending_changes.push(MarketChange {
            sequence: self.event_sequence,
            event: Some(MarketEvent::Observation(observation.clone())),
            view: Some(MarketViewUpdate::Observation(observation)),
        });
        Ok(self.event_sequence.get())
    }

    fn observation_is_selected(&self, observation: &MarketObservation) -> bool {
        let qualifier = observation.qualifier();
        let kind = observation.kind();
        self.static_subscriptions.values().any(|subscription| {
            subscription.members.contains_key(observation.market_id())
                && selector_matches_observation(&subscription.selectors, kind, qualifier)
        }) || self.dynamic_intents.values().any(|intent| {
            intent.members.contains_key(observation.market_id())
                && selector_matches_observation(&intent.selectors, kind, qualifier)
        }) || (self.static_subscriptions.is_empty() && self.dynamic_intents.is_empty())
    }
}
