use super::actor_task::MarketActorTask;
use crate::services::publication::EventPublication;

impl MarketActorTask {
    pub(super) async fn shutdown(
        &mut self,
        event_publication: &mut EventPublication,
    ) -> Result<(), String> {
        self.application
            .shutdown_sources(self.shutdown_timeout)
            .await?;
        self.publish_changes(event_publication).await?;
        event_publication.drain(self.shutdown_timeout).await?;
        self.history_recorder.shutdown().await
    }
}
