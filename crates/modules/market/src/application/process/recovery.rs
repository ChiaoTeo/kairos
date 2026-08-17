use serde_json::json;

use super::actor_task::{log_event, MarketActorTask};
use super::ingress::rollback_subscribe_intent;
use crate::services::control::MarketHttpResponse;
use crate::services::source::messages::SourceInput;

impl MarketActorTask {
    pub(super) async fn reconcile_control_sources(
        &mut self,
        path: &str,
        body: &str,
        mut response: MarketHttpResponse,
    ) -> MarketHttpResponse {
        let changes_subscriptions = matches!(
            path,
            "/v1/subscribe"
                | "/v1/subscriptions"
                | "/v1/unsubscribe"
                | "/v1/subscriptions/release-owner"
        ) || path.starts_with("/v1/subscriptions/")
            && (200..300).contains(&response.status);
        if changes_subscriptions {
            if let Some(activator) = self.source_activator.as_deref_mut() {
                if let Err(error) = self
                    .application
                    .activate_sources_for_subscriptions(activator)
                    .await
                {
                    rollback_subscribe_intent(&mut self.application, body);
                    response = MarketHttpResponse {
                        status: 422,
                        payload: json!({"error":{"code":"market.source_unavailable","message":error.to_string(),"retryable":true}}),
                    };
                }
            }
            if let Err(error) = self.application.sync_source_subscriptions().await {
                if response.status < 300 && matches!(path, "/v1/subscribe" | "/v1/subscriptions") {
                    rollback_subscribe_intent(&mut self.application, body);
                    response = MarketHttpResponse {
                        status: 422,
                        payload: json!({"error":{"code":"market.subscription_unavailable","message":error.to_string(),"retryable":true}}),
                    };
                }
                log_event(
                    "warn",
                    "market source reconciliation deferred",
                    json!({"error": error.to_string()}),
                );
            }
            self.refresh_subscription_status(&mut response);
        }
        response
    }

    pub(super) async fn handle_source_input(&mut self, input: SourceInput) -> Result<(), String> {
        self.application
            .apply_source_input(input)
            .await
            .map_err(|error| error.to_string())?;
        if let Err(error) = self.application.sync_source_subscriptions().await {
            log_event(
                "warn",
                "market source reconciliation deferred",
                json!({"error": error.to_string()}),
            );
        }
        Ok(())
    }
}
