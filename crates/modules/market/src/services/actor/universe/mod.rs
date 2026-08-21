use std::collections::{BTreeMap, BTreeSet};

use super::MarketActor;
use crate::domain::market::{MarketSelectionQuery, ResolvedMarket};
use crate::domain::subscription::{ReconcileResult, SubscriptionId, SubscriptionMemberRequirement};

impl MarketActor {
    fn reconcile_market_universe_members(
        &mut self,
        markets: Vec<ResolvedMarket>,
    ) -> Result<BTreeMap<SubscriptionId, ReconcileResult>, String> {
        let intents: Vec<_> = self
            .dynamic_intents
            .iter()
            .map(|(id, intent)| (id.clone(), intent.query.clone(), intent.max_members))
            .collect();
        let mut results = BTreeMap::new();
        for (id, query, max_members) in intents {
            let selected = self.valid_members(query, markets.clone())?;
            let intent = self.dynamic_intents.get_mut(&id).expect("intent exists");
            if selected.len() > max_members {
                results.insert(
                    id,
                    ReconcileResult {
                        rejected: Some(format!(
                            "dynamic subscription member limit exceeded: {} > {}",
                            selected.len(),
                            max_members
                        )),
                        ..Default::default()
                    },
                );
                continue;
            }
            let previous = intent.members.clone();
            intent.members = selected.clone();
            intent
                .member_requirements
                .retain(|member, _| selected.contains_key(member));
            for member in selected.keys() {
                intent
                    .member_requirements
                    .entry(member.clone())
                    .or_insert(SubscriptionMemberRequirement::Required);
            }
            let diff = diff_members(&previous, &selected);
            if diff.added.len() + diff.removed.len() + diff.changed.len() > 0 {
                self.generation += 1;
            }
            results.insert(id, diff);
        }
        Ok(results)
    }

    pub fn apply_market_universe(
        &mut self,
        update: crate::application::ReconcileMarketUniverse,
    ) -> Result<BTreeMap<SubscriptionId, ReconcileResult>, String> {
        if update.generation < self.market_universe_generation
            || (update.generation == self.market_universe_generation
                && update.event_sequence <= self.market_universe_event_sequence)
        {
            return Ok(BTreeMap::new());
        }
        let mut market_universe = BTreeMap::new();
        for market in &update.markets {
            market.validate()?;
            let Some(market_id) = market.market_id() else {
                return Err("canonical market universe cannot contain a consolidated route".into());
            };
            let member_id = market.member_id();
            if market_universe
                .insert(member_id.clone(), market.clone())
                .is_some()
            {
                return Err(format!(
                    "market universe contains duplicate member {member_id} for market {market_id}",
                ));
            }
        }
        let result = self.reconcile_market_universe_members(update.markets)?;
        self.market_universe = market_universe;
        self.market_universe_generation = update.generation;
        self.market_universe_event_sequence = update.event_sequence;
        Ok(result)
    }

    pub(crate) fn market_universe(&self) -> Vec<ResolvedMarket> {
        self.market_universe.values().cloned().collect()
    }

    pub(super) fn valid_members(
        &self,
        query: MarketSelectionQuery,
        markets: Vec<ResolvedMarket>,
    ) -> Result<BTreeMap<String, ResolvedMarket>, String> {
        let mut selected = BTreeMap::new();
        for market in markets {
            market.validate()?;
            if query.matches(&market) {
                selected.insert(market.member_id(), market);
            }
        }
        Ok(selected)
    }
}

pub(super) fn diff_members(
    previous: &BTreeMap<String, ResolvedMarket>,
    current: &BTreeMap<String, ResolvedMarket>,
) -> ReconcileResult {
    let previous_ids: BTreeSet<_> = previous.keys().cloned().collect();
    let current_ids: BTreeSet<_> = current.keys().cloned().collect();
    let changed: BTreeSet<_> = current_ids
        .intersection(&previous_ids)
        .filter(|market_id| previous.get(*market_id) != current.get(*market_id))
        .cloned()
        .collect();
    ReconcileResult {
        added: current_ids.difference(&previous_ids).cloned().collect(),
        removed: previous_ids.difference(&current_ids).cloned().collect(),
        changed: changed.iter().cloned().collect(),
        unchanged: current_ids
            .intersection(&previous_ids)
            .filter(|market_id| !changed.contains(*market_id))
            .cloned()
            .collect(),
        rejected: None,
    }
}
