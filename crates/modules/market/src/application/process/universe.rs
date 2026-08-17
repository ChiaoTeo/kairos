use serde_json::json;

use super::actor_task::{log_event, MarketActorTask};
use crate::application::ReconcileMarketUniverse;

impl MarketActorTask {
    pub(super) async fn handle_universe_update(
        &mut self,
        update: ReconcileMarketUniverse,
    ) -> Result<(), String> {
        self.application
            .reconcile_market_universe(update)
            .map_err(|error| error.to_string())?;
        if let Some(activator) = self.source_activator.as_deref_mut() {
            self.application
                .activate_sources_for_subscriptions(activator)
                .await
                .map_err(|error| error.to_string())?;
        }
        if let Err(error) = self.application.sync_source_subscriptions().await {
            log_event(
                "warn",
                "market source reconciliation after market-universe update deferred",
                json!({"error": error.to_string()}),
            );
        }
        Ok(())
    }
}
