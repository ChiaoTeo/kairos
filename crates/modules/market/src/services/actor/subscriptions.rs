use std::collections::BTreeMap;

use super::universe::diff_members;
use super::{MarketActor, PendingSourceRequest};
use crate::domain::market::{MarketSelectionQuery, ResolvedMarket};
use crate::domain::observation::identity::validate_observation_selectors;
use crate::domain::source::SourceStatus;
use crate::domain::subscription::{
    derive_subscription_status, ObservationSelector, ReconcileResult, SubscriptionId,
    SubscriptionMemberRequirement, SubscriptionMemberStatus, SubscriptionMode, SubscriptionState,
};

pub(super) struct DynamicIntent {
    pub(super) owner_id: String,
    pub(super) query: MarketSelectionQuery,
    pub(super) selectors: Vec<ObservationSelector>,
    pub(super) max_members: usize,
    pub(super) members: BTreeMap<String, ResolvedMarket>,
    pub(super) member_requirements: BTreeMap<String, SubscriptionMemberRequirement>,
}

impl MarketActor {
    pub fn subscribe_static(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: ResolvedMarket,
    ) -> Result<(), String> {
        self.subscribe_static_with_selectors(id, owner_id, market, Vec::new())
    }

    pub fn subscribe_static_with_selectors(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        market: ResolvedMarket,
        selectors: Vec<ObservationSelector>,
    ) -> Result<(), String> {
        if self.static_subscriptions.contains_key(&id) || self.dynamic_intents.contains_key(&id) {
            return Err(format!("subscription id already exists: {}", id.0));
        }
        market.validate()?;
        validate_observation_selectors(market.instrument_kind, &selectors)?;
        let owner_id = owner_id.into();
        if owner_id.trim().is_empty() {
            return Err("subscription owner is required".into());
        }
        let mut members = BTreeMap::new();
        let market_id = market.member_id();
        members.insert(market_id.clone(), market);
        self.static_subscriptions.insert(
            id.clone(),
            SubscriptionState {
                id,
                owner_id,
                mode: SubscriptionMode::Static,
                query: None,
                selectors,
                members,
                member_requirements: [(market_id, SubscriptionMemberRequirement::Required)]
                    .into_iter()
                    .collect(),
                member_status: BTreeMap::new(),
                status: Default::default(),
            },
        );
        self.generation += 1;
        Ok(())
    }

    pub fn subscribe_dynamic(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        members: Vec<ResolvedMarket>,
    ) -> Result<ReconcileResult, String> {
        self.subscribe_dynamic_with_selectors(id, owner_id, query, members, Vec::new())
    }

    pub fn subscribe_dynamic_with_selectors(
        &mut self,
        id: SubscriptionId,
        owner_id: impl Into<String>,
        query: MarketSelectionQuery,
        members: Vec<ResolvedMarket>,
        selectors: Vec<ObservationSelector>,
    ) -> Result<ReconcileResult, String> {
        if self.static_subscriptions.contains_key(&id) || self.dynamic_intents.contains_key(&id) {
            return Err(format!("subscription id already exists: {}", id.0));
        }
        let owner_id = owner_id.into();
        if owner_id.trim().is_empty() {
            return Err("subscription owner is required".into());
        }
        let selected = self.valid_members(query.clone(), members)?;
        for market in selected.values() {
            validate_observation_selectors(market.instrument_kind, &selectors)?;
        }
        if selected.len() > self.max_dynamic_members {
            return Err(format!(
                "dynamic subscription has {} members; limit is {}",
                selected.len(),
                self.max_dynamic_members
            ));
        }
        let previous = self
            .dynamic_intents
            .get(&id)
            .map(|intent| intent.members.clone())
            .unwrap_or_default();
        self.dynamic_intents.insert(
            id,
            DynamicIntent {
                owner_id,
                query,
                selectors,
                max_members: self.max_dynamic_members,
                members: selected.clone(),
                member_requirements: selected
                    .keys()
                    .map(|key| (key.clone(), SubscriptionMemberRequirement::Required))
                    .collect(),
            },
        );
        self.generation += 1;
        Ok(diff_members(&previous, &selected))
    }

    pub fn unsubscribe(&mut self, id: &SubscriptionId) -> bool {
        let removed = self.static_subscriptions.remove(id).is_some()
            || self.dynamic_intents.remove(id).is_some();
        if removed {
            self.generation += 1;
        }
        removed
    }

    pub fn unsubscribe_owned(
        &mut self,
        id: &SubscriptionId,
        owner_id: &str,
    ) -> Result<bool, String> {
        let existing_owner = self
            .static_subscriptions
            .get(id)
            .map(|subscription| subscription.owner_id.as_str())
            .or_else(|| {
                self.dynamic_intents
                    .get(id)
                    .map(|intent| intent.owner_id.as_str())
            });
        let Some(existing_owner) = existing_owner else {
            return Ok(false);
        };
        if existing_owner != owner_id {
            return Err(format!(
                "subscription {} belongs to a different owner",
                id.0
            ));
        }
        Ok(self.unsubscribe(id))
    }

    pub fn release_owner(&mut self, owner_id: &str) -> Vec<SubscriptionId> {
        let mut removed = self
            .static_subscriptions
            .iter()
            .filter(|(_, subscription)| subscription.owner_id == owner_id)
            .map(|(id, _)| id.clone())
            .chain(
                self.dynamic_intents
                    .iter()
                    .filter(|(_, intent)| intent.owner_id == owner_id)
                    .map(|(id, _)| id.clone()),
            )
            .collect::<Vec<_>>();
        removed.sort();
        removed.dedup();
        for id in &removed {
            self.static_subscriptions.remove(id);
            self.dynamic_intents.remove(id);
        }
        if !removed.is_empty() {
            self.generation += 1;
        }
        removed
    }

    pub fn set_member_requirement(
        &mut self,
        subscription_id: &SubscriptionId,
        member_id: impl Into<String>,
        requirement: SubscriptionMemberRequirement,
    ) -> Result<(), String> {
        let member_id = member_id.into();
        if let Some(subscription) = self.static_subscriptions.get_mut(subscription_id) {
            if !subscription.members.contains_key(&member_id) {
                return Err(format!("subscription member is not present: {member_id}"));
            }
            subscription
                .member_requirements
                .insert(member_id, requirement);
            self.generation += 1;
            return Ok(());
        }
        if let Some(intent) = self.dynamic_intents.get_mut(subscription_id) {
            if !intent.members.contains_key(&member_id) {
                return Err(format!("subscription member is not present: {member_id}"));
            }
            intent.member_requirements.insert(member_id, requirement);
            self.generation += 1;
            return Ok(());
        }
        Err(format!("subscription not found: {}", subscription_id.0))
    }

    pub(crate) fn subscription_states(&self) -> Vec<SubscriptionState> {
        let mut subscriptions = self
            .static_subscriptions
            .values()
            .map(|subscription| self.with_subscription_status(subscription))
            .collect::<Vec<_>>();
        subscriptions.extend(self.dynamic_intents.iter().map(|(id, intent)| {
            let member_status =
                self.subscription_member_status(id, &intent.members, &intent.member_requirements);
            SubscriptionState {
                id: id.clone(),
                owner_id: intent.owner_id.clone(),
                mode: SubscriptionMode::Dynamic,
                query: Some(intent.query.clone()),
                selectors: intent.selectors.clone(),
                members: intent.members.clone(),
                member_requirements: intent.member_requirements.clone(),
                status: derive_subscription_status(&intent.member_requirements, &member_status),
                member_status,
            }
        }));
        subscriptions.sort_by(|left, right| left.id.cmp(&right.id));
        subscriptions
    }

    fn with_subscription_status(&self, subscription: &SubscriptionState) -> SubscriptionState {
        let member_status = self.subscription_member_status(
            &subscription.id,
            &subscription.members,
            &subscription.member_requirements,
        );
        SubscriptionState {
            member_status: member_status.clone(),
            status: derive_subscription_status(&subscription.member_requirements, &member_status),
            ..subscription.clone()
        }
    }

    fn subscription_member_status(
        &self,
        subscription_id: &SubscriptionId,
        members: &BTreeMap<String, ResolvedMarket>,
        _requirements: &BTreeMap<String, SubscriptionMemberRequirement>,
    ) -> BTreeMap<String, SubscriptionMemberStatus> {
        members
            .iter()
            .map(|(market_id, market)| {
                let matches = self
                    .attached_sources
                    .iter()
                    .filter(|(_, source)| {
                        crate::application::source_accepts(&source.descriptor, market)
                    })
                    .collect::<Vec<_>>();
                let status = match matches.as_slice() {
                    [] => SubscriptionMemberStatus::Unavailable,
                    [(_, source)] => {
                        let confirmed = source
                            .confirmed
                            .contains_key(&(subscription_id.clone(), market_id.clone()));
                        let pending =
                            self.pending_for_subscription_market(subscription_id, market_id);
                        let source_status = self
                            .sources
                            .get(&source.descriptor.id)
                            .map(|state| state.status);
                        if confirmed {
                            match source_status {
                                Some(SourceStatus::Degraded | SourceStatus::Reconnecting) => {
                                    SubscriptionMemberStatus::Degraded
                                }
                                Some(SourceStatus::Stopped) => {
                                    SubscriptionMemberStatus::Unavailable
                                }
                                _ => SubscriptionMemberStatus::Ready,
                            }
                        } else if pending
                            || matches!(
                                source_status,
                                Some(SourceStatus::Starting | SourceStatus::WarmingUp)
                            )
                        {
                            SubscriptionMemberStatus::Pending
                        } else if matches!(
                            source_status,
                            Some(SourceStatus::Degraded | SourceStatus::Reconnecting)
                        ) {
                            SubscriptionMemberStatus::Degraded
                        } else {
                            SubscriptionMemberStatus::Unavailable
                        }
                    }
                    _ => SubscriptionMemberStatus::Rejected,
                };
                (market_id.clone(), status)
            })
            .collect()
    }

    fn pending_for_subscription_market(
        &self,
        subscription_id: &SubscriptionId,
        market_id: &str,
    ) -> bool {
        self.pending_source_requests.values().any(|pending| {
            matches!(pending, PendingSourceRequest::Subscribe { key, .. } if key.0 == *subscription_id && key.1 == market_id)
        })
    }
}
