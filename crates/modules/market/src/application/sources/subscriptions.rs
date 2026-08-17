use std::collections::{BTreeMap, BTreeSet};

use super::super::{MarketApplication, MarketError};
use crate::domain::market::ResolvedMarket;
use crate::domain::source::{SourceDescriptor, SourceId, SourceRouteKey};
use crate::services::actor::{BusinessSubscriptionKey, PendingSourceRequest};
use crate::services::source::messages::SourceCommand;
use crate::services::source::SourceActivator;

impl MarketApplication {
    pub async fn sync_source_subscriptions(&mut self) -> Result<(), MarketError> {
        self.reconcile_source_commands()
            .await
            .map_err(MarketError::Invalid)
    }

    pub(crate) async fn activate_sources_for_subscriptions(
        &mut self,
        activator: &mut dyn SourceActivator,
    ) -> Result<(), MarketError> {
        let markets = self
            .current_view()
            .subscriptions
            .into_iter()
            .flat_map(|subscription| subscription.members.into_values())
            .map(|market| (SourceRouteKey::from_market(&market), market))
            .collect::<BTreeMap<_, _>>()
            .into_values();
        for market in markets {
            let matched = self
                .actor
                .attached_sources
                .values()
                .any(|source| source_accepts(&source.descriptor, &market));
            if matched {
                continue;
            }
            let handle = activator
                .activate(&market, self.source_input_capacity())
                .await
                .map_err(MarketError::SourceUnavailable)?;
            self.attach_source(handle)
                .map_err(MarketError::SourceUnavailable)?;
        }
        Ok(())
    }

    async fn reconcile_source_commands(&mut self) -> Result<(), String> {
        let subscriptions = self.current_view().subscriptions;
        let mut desired =
            BTreeMap::<SourceId, BTreeMap<BusinessSubscriptionKey, ResolvedMarket>>::new();
        for subscription in subscriptions {
            for (market_id, market) in subscription.members {
                let matches = self
                    .actor
                    .attached_sources
                    .values()
                    .filter(|source| source_accepts(&source.descriptor, &market))
                    .map(|source| source.descriptor.id.clone())
                    .collect::<Vec<_>>();
                let source_id = match matches.as_slice() {
                    [source_id] => source_id.clone(),
                    [] => continue,
                    _ => {
                        return Err(format!(
                            "market {} matches multiple configured sources; select source_id",
                            market.market_id
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
    let source_id_matches = source.exchange_id.is_none()
        || market
            .source_id
            .as_ref()
            .is_none_or(|id| id.as_str().eq_ignore_ascii_case(source.id.as_str()));
    source_id_matches
        && source.exchange_id.as_ref().is_none_or(|exchange| {
            exchange
                .as_str()
                .strip_prefix("exchange:")
                .unwrap_or(exchange.as_str())
                .eq_ignore_ascii_case(
                    market
                        .exchange_id
                        .as_str()
                        .strip_prefix("exchange:")
                        .unwrap_or(market.exchange_id.as_str()),
                )
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
