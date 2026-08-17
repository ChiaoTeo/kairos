use crate::domain::events::{MarketChange, MarketEvent};
use kairos_primitives::Sequence;

use super::MarketActor;

impl MarketActor {
    pub(crate) fn drain_events(&mut self) -> Vec<(Sequence, MarketEvent)> {
        self.drain_changes()
            .into_iter()
            .filter_map(|change| change.event.map(|event| (change.sequence, event)))
            .collect()
    }

    pub(crate) fn drain_changes(&mut self) -> Vec<MarketChange> {
        std::mem::take(&mut self.pending_changes)
    }

    pub(crate) fn drain_events_limited(&mut self, limit: usize) -> Vec<(Sequence, MarketEvent)> {
        if limit == 0 {
            return Vec::new();
        }
        self.drain_changes_limited(limit)
            .into_iter()
            .filter_map(|change| change.event.map(|event| (change.sequence, event)))
            .collect()
    }

    pub(crate) fn drain_changes_limited(&mut self, limit: usize) -> Vec<MarketChange> {
        if limit == 0 {
            return Vec::new();
        }
        let count = self.pending_changes.len().min(limit);
        self.pending_changes.drain(..count).collect()
    }

    pub(crate) fn event_sequence(&self) -> Sequence {
        self.event_sequence
    }
}
