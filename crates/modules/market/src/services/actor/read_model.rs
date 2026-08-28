use kairos_primitives::runtime::ActorId;

use super::MarketActor;
use crate::domain::source::derive_readiness;
use crate::domain::view::MarketView;

impl MarketActor {
    pub(crate) fn actor_id(&self) -> &str {
        &self.actor_id
    }

    pub(crate) fn order_books(&self) -> &std::collections::BTreeMap<String, crate::OrderBook> {
        &self.order_books
    }

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
                            provider: value.provider.clone(),
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
            readiness: derive_readiness(self.sources.values()),
            feed_status: self.feed_status,
        }
    }
}
