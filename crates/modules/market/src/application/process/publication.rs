use crate::domain::events::MarketChange;

pub trait MarketChangePublisher: Send {
    fn publish(&mut self, change: &MarketChange) -> Result<(), String>;
}

pub(super) enum MarketHistoryRecorder {
    Noop,
    Queue(crate::services::publication::HistoryQueue),
}

impl MarketHistoryRecorder {
    pub(super) async fn record(
        &mut self,
        events: &[(u64, crate::MarketEvent)],
    ) -> Result<(), String> {
        match self {
            Self::Noop => Ok(()),
            Self::Queue(queue) => queue.record(events).await,
        }
    }

    pub(super) async fn shutdown(&mut self) -> Result<(), String> {
        match self {
            Self::Noop => Ok(()),
            Self::Queue(queue) => queue.shutdown().await,
        }
    }
}

use super::actor_task::MarketActorTask;
use crate::services::publication::EventPublication;

impl MarketActorTask {
    pub(super) async fn publish_changes(
        &mut self,
        event_publication: &mut EventPublication,
    ) -> Result<(), String> {
        let changes = self.application.drain_changes_limited(1_024);
        let events = changes
            .iter()
            .filter_map(|change| {
                change
                    .event
                    .clone()
                    .map(|event| (change.sequence.get(), event))
            })
            .collect::<Vec<_>>();
        self.history_recorder.record(&events).await?;
        event_publication.publish(events)?;
        for change in changes {
            if change.view.is_some() {
                self.publisher.publish(&change)?;
            }
        }
        Ok(())
    }
}
