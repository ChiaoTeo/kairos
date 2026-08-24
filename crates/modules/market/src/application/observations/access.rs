use super::super::{MarketApplication, MarketQueryResult};
use crate::domain::events::MarketEvent;

impl MarketApplication {
    pub fn current_view(&self) -> crate::domain::view::MarketView {
        self.actor.current_view()
    }

    pub(crate) fn checkpoint(&self) -> crate::services::actor::ReplayCheckpoint {
        self.actor.checkpoint()
    }

    pub fn event_sequence(&self) -> u64 {
        self.actor.event_sequence().get()
    }

    pub fn query(&self) -> MarketQueryResult {
        MarketQueryResult::new(self.actor.current_view())
    }

    pub fn drain_events(&mut self) -> Vec<(u64, MarketEvent)> {
        self.actor
            .drain_events()
            .into_iter()
            .map(|(sequence, event)| (sequence.get(), event))
            .collect()
    }

    pub fn drain_events_limited(&mut self, limit: usize) -> Vec<(u64, MarketEvent)> {
        self.actor
            .drain_events_limited(limit)
            .into_iter()
            .map(|(sequence, event)| (sequence.get(), event))
            .collect()
    }

    pub fn drain_changes_limited(
        &mut self,
        limit: usize,
    ) -> Vec<crate::domain::events::MarketChange> {
        self.actor.drain_changes_limited(limit)
    }
}
