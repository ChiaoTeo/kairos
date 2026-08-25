use std::collections::{BTreeMap, BTreeSet};

use super::super::{MarketApplication, MarketError};
use crate::domain::market::ResolvedMarket;
use crate::domain::source::{FeedDescriptor, MarketFeedId};
use crate::services::actor::{PendingSourceRequest, PhysicalSubscriptionKey};
use crate::services::source::messages::SourceCommand;

impl MarketApplication {
    pub async fn sync_source_subscriptions(&mut self) -> Result<(), MarketError> {
        self.reconcile_source_commands()
            .await
            .map_err(MarketError::Invalid)
    }

    async fn reconcile_source_commands(&mut self) -> Result<(), String> {
        let mut desired = self.desired_source_subscriptions()?;
        let pending_keys = self
            .actor
            .pending_source_requests
            .values()
            .filter_map(|pending| match pending {
                PendingSourceRequest::Subscribe { source_id, key }
                | PendingSourceRequest::Unsubscribe { source_id, key } => {
                    Some((source_id.clone(), key.clone()))
                },
                PendingSourceRequest::ResyncOrderBook { .. } => None,
            })
            .collect::<BTreeSet<_>>();
        let source_ids = self
            .actor
            .attached_sources
            .keys()
            .cloned()
            .collect::<Vec<_>>();
        for source_id in source_ids {
            let managed = self.actor.attached_sources[&source_id].inputs.is_none()
                && self.actor.attached_sources[&source_id].task.is_none();
            if managed {
                continue;
            }
            if self.actor.source_is_stopped(&source_id)
                || self.actor.source_command_closed(&source_id)
            {
                continue;
            }
            let wanted = desired.remove(&source_id).unwrap_or_default();
            let stale = self.actor.attached_sources[&source_id]
                .confirmed
                .keys()
                .filter(|key| !wanted.contains_key(*key))
                .cloned()
                .collect::<Vec<_>>();
            for key in stale {
                if pending_keys.contains(&(source_id.clone(), key.clone())) {
                    continue;
                }
                let handle = self.actor.attached_sources[&source_id].confirmed[&key].clone();
                let request_id = self.next_request_id();
                self.actor.attached_sources[&source_id]
                    .commands
                    .send(SourceCommand::Unsubscribe { request_id, handle })
                    .await
                    .map_err(|_| format!("market source command channel closed: {source_id}"))?;
                self.actor.pending_source_requests.insert(
                    request_id,
                    PendingSourceRequest::Unsubscribe {
                        source_id: source_id.clone(),
                        key,
                    },
                );
            }
            for (key, market) in wanted {
                if self.actor.attached_sources[&source_id]
                    .confirmed
                    .contains_key(&key)
                    || pending_keys.contains(&(source_id.clone(), key.clone()))
                {
                    continue;
                }
                let request_id = self.next_request_id();
                self.actor.attached_sources[&source_id]
                    .commands
                    .send(SourceCommand::Subscribe {
                        request_id,
                        market: Box::new(market),
                    })
                    .await
                    .map_err(|_| format!("market source command channel closed: {source_id}"))?;
                self.actor.pending_source_requests.insert(
                    request_id,
                    PendingSourceRequest::Subscribe {
                        source_id: source_id.clone(),
                        key,
                    },
                );
            }
        }
        Ok(())
    }

    pub(crate) fn desired_source_subscriptions(
        &self,
    ) -> Result<BTreeMap<MarketFeedId, BTreeMap<PhysicalSubscriptionKey, ResolvedMarket>>, String>
    {
        let subscriptions = self.current_view().subscriptions;
        let mut desired =
            BTreeMap::<MarketFeedId, BTreeMap<PhysicalSubscriptionKey, ResolvedMarket>>::new();
        for subscription in subscriptions {
            let selectors = subscription.selectors.clone();
            for (_market_id, mut market) in subscription.members {
                let route_matches = self
                    .actor
                    .attached_sources
                    .values()
                    .filter(|source| source_accepts(&source.descriptor, &market))
                    .collect::<Vec<_>>();
                let has_route_match = !route_matches.is_empty();
                let mut matches = route_matches
                    .into_iter()
                    .filter(|source| source_supports_selectors(&source.descriptor, &selectors))
                    .collect::<Vec<_>>();
                if matches.is_empty() && has_route_match {
                    return Err(format!(
                        "configured source for market {} does not support the requested observation",
                        market.scope.key()
                    ));
                }
                matches.sort_by_key(|source| {
                    (
                        self.actor
                            .source_state(&source.descriptor.id)
                            .is_none_or(|state| {
                                state.status != crate::domain::source::SourceStatus::Ready
                            }),
                        source.descriptor.id.clone(),
                    )
                });
                let Some(source_id) = matches.first().map(|source| source.descriptor.id.clone())
                else {
                    continue;
                };
                let descriptor = &self.actor.attached_sources[&source_id].descriptor;
                if descriptor.provider.is_some() {
                    market.select_observations(&selectors)?;
                }
                let key =
                    PhysicalSubscriptionKey::for_source(descriptor, &market).ok_or_else(|| {
                        format!(
                            "market {} cannot resolve physical route for source {source_id}",
                            market.scope.key()
                        )
                    })?;
                desired
                    .entry(source_id)
                    .or_default()
                    .entry(key)
                    .or_insert(market);
            }
        }
        Ok(desired)
    }
}

pub(crate) fn source_accepts(source: &FeedDescriptor, market: &ResolvedMarket) -> bool {
    let Some(provider) = source.provider.as_ref() else {
        // Replay and derived feeds are intentionally providerless. Their
        // eligibility comes from the already-selected canonical Market, not
        // from a live provider attachment.
        return true;
    };
    let Some(attachment) = market.attach_route(&source.id, provider) else {
        return false;
    };
    source
        .provider
        .as_ref()
        .is_none_or(|provider| provider == &attachment.route.provider)
        && source
            .market_type
            .as_ref()
            .is_none_or(|market_type| market_type == &attachment.provider_segment)
}

pub(crate) fn source_supports_selectors(
    source: &FeedDescriptor,
    selectors: &[crate::domain::subscription::ObservationSelector],
) -> bool {
    source.observation_capabilities.is_empty()
        || selectors.iter().all(|selector| {
            selector.kind.is_none_or(|kind| {
                source.observation_capabilities.contains(&kind)
                    || matches!(
                        kind,
                        crate::ObservationKind::Rate
                            if source
                                .observation_capabilities
                                .contains(&crate::ObservationKind::FundingRate)
                    )
                    || matches!(
                        kind,
                        crate::ObservationKind::FundingRate
                            if source
                                .observation_capabilities
                                .contains(&crate::ObservationKind::Rate)
                    )
            })
        })
}

#[cfg(test)]
mod tests {
    use kairos_primitives::reference::InstrumentKind;

    use super::*;
    use crate::domain::market::ProviderRouteBinding;

    fn equity_market() -> ResolvedMarket {
        let mut market = ResolvedMarket::new_with_binding(
            "market:exchange:nasdaq:equity:AAPL",
            "instrument:equity:US:AAPL:common",
            InstrumentKind::Equity,
            "exchange:nasdaq",
            ProviderRouteBinding::new("massive", "equity", "AAPL").unwrap(),
        )
        .unwrap();
        market.asset_type = Some(kairos_primitives::reference::AssetClass::Equity);
        market
    }

    #[test]
    fn provider_scoped_source_accepts_canonical_exchange_market() {
        let source = FeedDescriptor::for_provider(
            MarketFeedId::new("massive-equity").unwrap(),
            "massive",
            kairos_primitives::reference::ExchangeId::new("data_provider:massive").unwrap(),
            "equity",
            Some("equity".into()),
        )
        .unwrap();

        assert!(source_accepts(&source, &equity_market()));
    }

    #[test]
    fn feed_for_another_provider_does_not_match_the_route() {
        let source = FeedDescriptor::for_provider(
            MarketFeedId::new("nasdaq-equity").unwrap(),
            "other-provider",
            kairos_primitives::reference::ExchangeId::new("exchange:nyse").unwrap(),
            "equity",
            Some("equity".into()),
        )
        .unwrap();

        assert!(!source_accepts(&source, &equity_market()));
    }

    #[test]
    fn managed_source_plan_aggregates_logical_consumers_by_physical_route() {
        let mut application = MarketApplication::new("market", 10).unwrap();
        let source_id = MarketFeedId::new("massive-equity").unwrap();
        application
            .attach_managed_source(
                FeedDescriptor::for_provider(
                    source_id.clone(),
                    "massive",
                    kairos_primitives::reference::ExchangeId::new("data_provider:massive").unwrap(),
                    "equity",
                    Some("equity".into()),
                )
                .unwrap(),
            )
            .unwrap();
        let market = equity_market();
        application
            .subscribe_static(
                crate::SubscriptionId::new("strategy-a-aapl").unwrap(),
                "strategy-a",
                market.clone(),
            )
            .unwrap();
        application
            .subscribe_static(
                crate::SubscriptionId::new("strategy-b-aapl").unwrap(),
                "strategy-b",
                market,
            )
            .unwrap();

        let desired = application.desired_source_subscriptions().unwrap();
        assert_eq!(desired.len(), 1);
        assert_eq!(desired[&source_id].len(), 1);
    }

    #[test]
    fn managed_source_plan_compiles_strategy_observations_into_physical_identity() {
        let mut application = MarketApplication::new("market", 10).unwrap();
        let source_id = MarketFeedId::new("massive-equity").unwrap();
        let source = FeedDescriptor::for_provider(
            source_id.clone(),
            "massive",
            kairos_primitives::reference::ExchangeId::new("data_provider:massive").unwrap(),
            "equity",
            Some("equity".into()),
        )
        .unwrap()
        .with_observation_capabilities([
            crate::ObservationKind::Quote,
            crate::ObservationKind::Trade,
        ]);
        application.attach_managed_source(source).unwrap();
        let mut market = equity_market();
        let provider = kairos_primitives::market::Provider::new("massive").unwrap();
        market
            .runtime_routes
            .get_mut(&provider)
            .unwrap()
            .observation_capabilities =
            [crate::ObservationKind::Quote, crate::ObservationKind::Trade]
                .into_iter()
                .collect();
        application
            .subscribe_static_with_selectors(
                crate::SubscriptionId::new("strategy-a-aapl").unwrap(),
                "strategy-a",
                market.clone(),
                vec![crate::ObservationSelector::parse("quote").unwrap()],
            )
            .unwrap();
        application
            .subscribe_static_with_selectors(
                crate::SubscriptionId::new("strategy-b-aapl").unwrap(),
                "strategy-b",
                market,
                vec![crate::ObservationSelector::parse("trade").unwrap()],
            )
            .unwrap();

        let desired = application.desired_source_subscriptions().unwrap();
        assert_eq!(desired[&source_id].len(), 2);
        let requirements = desired[&source_id]
            .values()
            .map(ResolvedMarket::observation_requirements)
            .collect::<BTreeSet<_>>();
        assert!(requirements.contains(&BTreeSet::from([
            crate::ObservationSelector::parse("quote").unwrap()
        ])));
        assert!(requirements.contains(&BTreeSet::from([
            crate::ObservationSelector::parse("trade").unwrap()
        ])));
    }
}
