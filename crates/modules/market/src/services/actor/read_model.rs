use kairos_primitives::ActorId;

use crate::domain::{source::derive_readiness, view::MarketView};

use super::MarketActor;

impl MarketActor {
    pub fn current_view(&self) -> MarketView {
        MarketView {
            actor_id: ActorId::new(self.actor_id.clone()).expect("validated actor ID"),
            generation: self.generation,
            views: self.views.clone(),
            order_books: self.order_books.clone(),
            freshness: self
                .freshness
                .iter()
                .map(|(key, value)| {
                    (
                        key.clone(),
                        crate::domain::view::MarketViewFreshness {
                            source_id: value.source_id.clone(),
                            scope: value.scope.clone(),
                            data_kind: value.data_kind.clone(),
                            last_event_time_unix_nanos: value.last_event_time_unix_nanos,
                            last_received_time_unix_nanos: value.last_received_time_unix_nanos,
                            status: value.status,
                        },
                    )
                })
                .collect(),
            subscriptions: self.subscription_states(),
            sources: self.sources.clone(),
            readiness: derive_readiness(self.sources.values()),
            feed_status: self.feed_status,
        }
    }
}
