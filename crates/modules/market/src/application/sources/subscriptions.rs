use std::collections::{BTreeMap, BTreeSet};

use super::super::{MarketApplication, MarketError};
use crate::domain::market::ResolvedMarket;
use crate::domain::source::{SourceDescriptor, SourceId};
use crate::services::actor::{BusinessSubscriptionKey, PendingSourceRequest};
use crate::services::source::messages::SourceCommand;

impl MarketApplication {
    pub async fn sync_source_subscriptions(&mut self) -> Result<(), MarketError> {
        self.reconcile_source_commands()
            .await
            .map_err(MarketError::Invalid)
    }

    async fn reconcile_source_commands(&mut self) -> Result<(), String> {
        let subscriptions = self.current_view().subscriptions;
        let mut desired =
            BTreeMap::<SourceId, BTreeMap<BusinessSubscriptionKey, ResolvedMarket>>::new();
        for subscription in subscriptions {
            let selectors = subscription.selectors.clone();
            for (market_id, market) in subscription.members {
                let route_matches = self
                    .actor
                    .attached_sources
                    .values()
                    .filter(|source| source_accepts(&source.descriptor, &market))
                    .collect::<Vec<_>>();
                let matches = route_matches
                    .iter()
                    .filter(|source| source_supports_selectors(&source.descriptor, &selectors))
                    .map(|source| source.descriptor.id.clone())
                    .collect::<Vec<_>>();
                if matches.is_empty() && !route_matches.is_empty() {
                    return Err(format!(
                        "configured source for market {} does not support the requested observation",
                        market.scope.key()
                    ));
                }
                let source_id = match matches.as_slice() {
                    [source_id] => source_id.clone(),
                    [] => continue,
                    _ => {
                        return Err(format!(
                            "market {} matches multiple configured sources; select source_id",
                            market.scope.key()
                        ))
                    }
                };
                desired
                    .entry(source_id)
                    .or_default()
                    .insert((subscription.id.clone(), market_id), market);
            }
        }

        let pending_keys = self
            .actor
            .pending_source_requests
            .values()
            .filter_map(|pending| match pending {
                PendingSourceRequest::Subscribe { source_id, key }
                | PendingSourceRequest::Unsubscribe { source_id, key } => {
                    Some((source_id.clone(), key.clone()))
                }
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
}

pub(crate) fn source_accepts(source: &SourceDescriptor, market: &ResolvedMarket) -> bool {
    let source_id_matches = market
        .source_id
        .as_ref()
        .is_none_or(|id| id.as_str().eq_ignore_ascii_case(source.id.as_str()));
    source_id_matches
        && source.exchange_id.as_ref().is_none_or(|exchange| {
            market.exchange_id.as_ref().is_some_and(|market_exchange| {
                exchange
                    .as_str()
                    .strip_prefix("exchange:")
                    .unwrap_or(exchange.as_str())
                    .eq_ignore_ascii_case(
                        market_exchange
                            .as_str()
                            .strip_prefix("exchange:")
                            .unwrap_or(market_exchange.as_str()),
                    )
            })
        })
        && source
            .market_type
            .as_ref()
            .is_none_or(|market_type| market_type == &market.route.provider_product)
        && source.asset_type.as_ref().is_none_or(|asset_type| {
            market
                .asset_type
                .as_ref()
                .is_none_or(|market_asset| market_asset == asset_type)
        })
}

pub(crate) fn source_supports_selectors(
    source: &SourceDescriptor,
    selectors: &[crate::domain::subscription::ObservationSelector],
) -> bool {
    source.observation_capabilities.is_empty()
        || selectors.iter().all(|selector| {
            selector
                .kind
                .is_none_or(|kind| source.observation_capabilities.contains(&kind))
        })
}
