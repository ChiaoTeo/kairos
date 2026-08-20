use std::collections::BTreeMap;

use kairos_primitives::time::{Generation, Sequence};

use crate::application::{
    AccountBusinessChange, AccountBusinessEvent, AccountCurrentView, AccountSegmentView,
};
use crate::domain::{
    Account, AccountEvent, AccountSegment, AccountSnapshot, AccountState, ApplyOutcome, Balance,
    SegmentKey, SignedQuantity, SnapshotKind,
};

pub(crate) struct ActorUndo {
    states: Vec<(SegmentKey, AccountState)>,
    generation: Generation,
    event_sequence: Sequence,
}

#[derive(Clone)]
pub(crate) struct AccountActor {
    actor_id: String,
    accounts: BTreeMap<SegmentKey, Account>,
    generation: Generation,
    event_sequence: Sequence,
}

impl AccountActor {
    pub(crate) fn new(
        segments: Vec<AccountSegment>,
        restored: Vec<(AccountSegment, crate::domain::AccountState)>,
        generation: Generation,
        event_sequence: Sequence,
    ) -> Result<Self, String> {
        let mut accounts: BTreeMap<SegmentKey, Account> = BTreeMap::new();
        let mut owner_account_id = None;
        for segment in segments {
            match &owner_account_id {
                Some(account_id) if account_id != &segment.identity.account_id => {
                    return Err(format!(
                        "one Account Actor cannot own multiple account ids: {account_id} and {}",
                        segment.identity.account_id
                    ));
                },
                None => owner_account_id = Some(segment.identity.account_id.clone()),
                _ => {},
            }
            let key = segment.segment_key.clone();
            if accounts.contains_key(&key) {
                return Err(format!("duplicate account segment: {key}"));
            }
            accounts.insert(
                key,
                Account::new(segment).map_err(|error| error.to_string())?,
            );
        }
        for (segment, state) in restored {
            let account = accounts.get_mut(&segment.segment_key).ok_or_else(|| {
                format!("stored segment is not configured: {}", segment.segment_key)
            })?;
            account.restore_state(state);
        }
        Ok(Self {
            actor_id: "account".into(),
            accounts,
            generation,
            event_sequence,
        })
    }

    pub(crate) fn record_fill(
        &mut self,
        fill: crate::domain::AccountFill,
    ) -> Result<ApplyOutcome, String> {
        let account = self
            .accounts
            .get_mut(&fill.segment_key)
            .ok_or_else(|| format!("fill segment is not configured: {}", fill.segment_key))?;
        let outcome = account
            .record_fill(fill)
            .map_err(|error| error.to_string())?;
        if outcome == ApplyOutcome::Applied {
            self.event_sequence += 1;
            self.generation += 1;
        }
        Ok(outcome)
    }

    pub(crate) fn apply_events(&mut self, event: AccountEvent) -> Result<u64, String> {
        let events = match event {
            AccountEvent::Batch(events) => events,
            event => vec![event],
        };
        let mut applied = 0_u64;
        for event in events {
            if self.apply_event(event)? == ApplyOutcome::Applied {
                applied += 1;
            }
        }
        self.event_sequence += applied;
        self.generation += u64::from(applied > 0);
        Ok(applied)
    }

    pub(crate) fn undo_for_events(&self, events: &[AccountEvent]) -> ActorUndo {
        let mut keys = Vec::new();
        let mut all_accounts = false;
        for event in events {
            collect_event_keys(event, &mut keys, &mut all_accounts);
        }
        let states = if all_accounts {
            self.accounts
                .iter()
                .map(|(key, account)| (key.clone(), account.state().clone()))
                .collect()
        } else {
            keys.sort();
            keys.dedup();
            keys.into_iter()
                .filter_map(|key| {
                    self.accounts
                        .get(&key)
                        .map(|account| (key, account.state().clone()))
                })
                .collect()
        };
        ActorUndo {
            states,
            generation: self.generation,
            event_sequence: self.event_sequence,
        }
    }

    pub(crate) fn restore_undo(&mut self, undo: ActorUndo) {
        for (key, state) in undo.states {
            if let Some(account) = self.accounts.get_mut(&key) {
                account.restore_state(state);
            }
        }
        self.generation = undo.generation;
        self.event_sequence = undo.event_sequence;
    }

    fn apply_event(&mut self, event: AccountEvent) -> Result<ApplyOutcome, String> {
        match event {
            AccountEvent::Snapshot(snapshot) => {
                let account = self
                    .accounts
                    .values_mut()
                    .find(|value| value.segment().segment_key == snapshot.segment_key)
                    .ok_or_else(|| {
                        format!("stream segment is not configured: {}", snapshot.segment_key)
                    })?;
                account
                    .apply_snapshot(snapshot)
                    .map_err(|error| error.to_string())
            },
            AccountEvent::EarnHoldings(snapshot) => {
                let account = self
                    .accounts
                    .get_mut(&snapshot.segment_key)
                    .ok_or_else(|| {
                        format!(
                            "Earn snapshot segment is not configured: {}",
                            snapshot.segment_key
                        )
                    })?;
                account
                    .apply_earn_snapshot(snapshot)
                    .map_err(|error| error.to_string())
            },
            AccountEvent::Fill(fill) => {
                let account = self.accounts.get_mut(&fill.segment_key).ok_or_else(|| {
                    format!("fill segment is not configured: {}", fill.segment_key)
                })?;
                account.record_fill(fill).map_err(|error| error.to_string())
            },
            AccountEvent::ObservedFill(fill) => {
                let account = self.accounts.get_mut(&fill.segment_key).ok_or_else(|| {
                    format!(
                        "observed fill segment is not configured: {}",
                        fill.segment_key
                    )
                })?;
                account
                    .observe_fill(fill)
                    .map_err(|error| error.to_string())
            },
            AccountEvent::OrderObserved(observation) => {
                let Some(account) = self.accounts.values_mut().find(|account| {
                    account
                        .state()
                        .open_orders()
                        .contains_key(&observation.order_id)
                }) else {
                    return Ok(ApplyOutcome::NoChange);
                };
                Ok(account.apply_order_observation(observation))
            },
            AccountEvent::SimulatedCapitalMutation(mutation) => {
                let account = self
                    .accounts
                    .get_mut(&mutation.segment_key)
                    .ok_or_else(|| {
                        format!(
                            "simulated Capital mutation segment is not configured: {}",
                            mutation.segment_key
                        )
                    })?;
                account
                    .apply_simulated_capital_mutation(mutation)
                    .map_err(|error| error.to_string())
            },
            AccountEvent::Batch(_) => Err("nested account event batch is not supported".into()),
        }
    }

    pub(crate) fn selected_segments(
        &self,
        account_id: &str,
        segments: &[String],
    ) -> Result<Vec<AccountSegment>, String> {
        let selected: Vec<_> = self
            .accounts
            .values()
            .filter(|account| {
                account.segment().identity.account_id == account_id
                    && segment_selected(segments, &account.segment().segment_key)
            })
            .map(|account| account.segment().clone())
            .collect();
        if selected.is_empty() {
            return Err(format!("no configured segments for account: {account_id}"));
        }
        Ok(selected)
    }

    pub(crate) fn apply_snapshot(
        &mut self,
        snapshot: AccountSnapshot,
    ) -> Result<(ApplyOutcome, Vec<crate::application::AccountDifference>), String> {
        let account = self
            .accounts
            .get_mut(&snapshot.segment_key)
            .ok_or_else(|| {
                format!(
                    "snapshot segment is not configured: {}",
                    snapshot.segment_key
                )
            })?;
        let differences = compare_snapshot(account, &snapshot);
        let outcome = account
            .apply_snapshot(snapshot)
            .map_err(|error| error.to_string())?;
        if outcome == ApplyOutcome::Applied {
            self.event_sequence += 1;
            self.generation += 1;
        }
        Ok((outcome, differences))
    }

    pub(crate) fn begin_reconciliation(
        &mut self,
        account_id: &str,
        segments: &[String],
    ) -> Result<bool, String> {
        let keys: Vec<_> = self
            .selected_segments(account_id, segments)?
            .into_iter()
            .map(|segment| segment.segment_key)
            .collect();
        let mut changed = false;
        for key in &keys {
            changed |= self
                .accounts
                .get_mut(key)
                .expect("key collected")
                .begin_reconciliation()
                == ApplyOutcome::Applied;
        }
        if changed {
            self.generation += 1;
            self.event_sequence += 1;
        }
        Ok(changed)
    }

    pub(crate) fn persistent_accounts(&self) -> Vec<Account> {
        self.accounts.values().cloned().collect()
    }

    pub(crate) fn persistence_metadata(&self) -> (&str, Generation, Sequence) {
        (&self.actor_id, self.generation, self.event_sequence)
    }

    pub(crate) fn projection(&self, segment_key: &SegmentKey) -> Option<AccountSegmentView> {
        self.accounts
            .get(segment_key)
            .map(AccountSegmentView::from_account)
    }

    pub(crate) fn has_simulated_capital_mutation(
        &self,
        segment_key: &SegmentKey,
        mutation_id: &kairos_primitives::runtime::IdempotencyKey,
    ) -> Result<bool, String> {
        self.accounts
            .get(segment_key)
            .map(|account| account.state().has_capital_mutation(mutation_id))
            .ok_or_else(|| format!("account segment is not configured: {segment_key}"))
    }

    pub fn current_view(&self) -> AccountCurrentView {
        AccountCurrentView {
            actor_id: kairos_primitives::runtime::ActorId::new(self.actor_id.clone()).unwrap(),
            generation: self.generation,
            event_sequence: self.event_sequence,
            segments: self
                .accounts
                .values()
                .map(AccountSegmentView::from_account)
                .collect(),
        }
    }

    /// Derive the explicit business facts produced by a completed Actor
    /// transition. The caller invokes this before publication and after
    /// persistence succeeds; no mmap payload participates in this operation.
    pub(crate) fn business_events_since(&self, previous: &Self) -> Vec<AccountBusinessEvent> {
        let first_sequence = previous.event_sequence.get().saturating_add(1);
        let last_sequence = self.event_sequence.get();
        if first_sequence > last_sequence {
            return Vec::new();
        }

        let account_ids = self
            .accounts
            .values()
            .map(|account| account.segment().identity.account_id.clone())
            .collect::<std::collections::BTreeSet<_>>();
        let mut final_changes = BTreeMap::<_, Vec<AccountBusinessChange>>::new();
        let mut occurred_at = BTreeMap::new();
        for (key, current) in &self.accounts {
            let current = AccountSegmentView::from_account(current);
            let old = previous
                .accounts
                .get(key)
                .map(AccountSegmentView::from_account);
            let changes = final_changes.entry(current.account_id.clone()).or_default();
            collect_business_changes(old.as_ref(), &current, changes);
            occurred_at
                .entry(current.account_id.clone())
                .and_modify(|value: &mut kairos_primitives::time::UnixNanos| {
                    *value = (*value).max(current.observed_at_unix_nanos)
                })
                .or_insert(current.observed_at_unix_nanos);
        }

        let mut events = Vec::new();
        for sequence in first_sequence..=last_sequence {
            for account_id in &account_ids {
                events.push(AccountBusinessEvent {
                    sequence: Sequence::new(sequence),
                    account_id: account_id.clone(),
                    occurred_at_unix_nanos: occurred_at
                        .get(account_id)
                        .copied()
                        .unwrap_or_default(),
                    changes: if sequence == last_sequence {
                        final_changes.remove(account_id).unwrap_or_default()
                    } else {
                        Vec::new()
                    },
                    provenance: None,
                });
            }
        }
        events
    }
}

fn collect_business_changes(
    old: Option<&AccountSegmentView>,
    current: &AccountSegmentView,
    out: &mut Vec<AccountBusinessChange>,
) {
    let old_balances = old
        .map(|value| {
            value
                .balances
                .iter()
                .map(|balance| (balance.asset_id.clone(), balance))
                .collect::<BTreeMap<_, _>>()
        })
        .unwrap_or_default();
    let current_balances = current
        .balances
        .iter()
        .map(|balance| (balance.asset_id.clone(), balance))
        .collect::<BTreeMap<_, _>>();
    for balance in current_balances.values() {
        if old_balances.get(&balance.asset_id).copied() != Some(*balance) {
            out.push(AccountBusinessChange::Balance {
                segment_key: current.segment_key.clone(),
                value: (*balance).clone(),
            });
        }
    }
    for (asset_id, removed) in &old_balances {
        if !current_balances.contains_key(asset_id) {
            out.push(AccountBusinessChange::BalanceRemoved {
                segment_key: current.segment_key.clone(),
                asset_id: removed.asset_id.clone(),
            });
        }
    }

    let old_positions = old
        .map(|value| {
            value
                .positions
                .iter()
                .map(|position| {
                    (
                        (position.instrument_id.clone(), position.position_side),
                        position,
                    )
                })
                .collect::<BTreeMap<_, _>>()
        })
        .unwrap_or_default();
    let current_positions = current
        .positions
        .iter()
        .map(|position| {
            (
                (position.instrument_id.clone(), position.position_side),
                position,
            )
        })
        .collect::<BTreeMap<_, _>>();
    for (key, position) in &current_positions {
        if old_positions.get(key).copied() != Some(*position) {
            out.push(AccountBusinessChange::Position {
                segment_key: current.segment_key.clone(),
                value: (*position).clone(),
            });
        }
    }
    for (key, removed) in &old_positions {
        if !current_positions.contains_key(key) {
            out.push(AccountBusinessChange::PositionRemoved {
                segment_key: current.segment_key.clone(),
                instrument_id: removed.instrument_id.clone(),
                market_id: removed.market_id.clone(),
                position_side: removed.position_side,
            });
        }
    }

    let old_earn = old
        .map(|value| {
            value
                .earn_holdings
                .iter()
                .map(|holding| (earn_holding_key(holding), holding))
                .collect::<BTreeMap<_, _>>()
        })
        .unwrap_or_default();
    let current_earn = current
        .earn_holdings
        .iter()
        .map(|holding| (earn_holding_key(holding), holding))
        .collect::<BTreeMap<_, _>>();
    for (key, holding) in &current_earn {
        if old_earn.get(key).copied() != Some(*holding) {
            out.push(AccountBusinessChange::EarnHolding {
                segment_key: current.segment_key.clone(),
                value: (*holding).clone(),
            });
        }
    }
    for key in old_earn.keys() {
        if !current_earn.contains_key(key) {
            out.push(AccountBusinessChange::EarnHoldingRemoved {
                segment_key: current.segment_key.clone(),
                holding_key: key.clone(),
            });
        }
    }

    let old_orders = old
        .map(|value| {
            value
                .open_orders
                .iter()
                .map(|order| (order.order_id.clone(), order))
                .collect::<BTreeMap<_, _>>()
        })
        .unwrap_or_default();
    let current_orders = current
        .open_orders
        .iter()
        .map(|order| (order.order_id.clone(), order))
        .collect::<BTreeMap<_, _>>();
    for order in current_orders.values() {
        if old_orders.get(&order.order_id).copied() != Some(*order) {
            out.push(AccountBusinessChange::ObservedOrder {
                segment_key: current.segment_key.clone(),
                value: (*order).clone(),
            });
        }
    }
    for (order_id, order) in &old_orders {
        if !current_orders.contains_key(order_id) {
            out.push(AccountBusinessChange::ObservedOrderRemoved {
                segment_key: current.segment_key.clone(),
                order_id: order.order_id.clone(),
                remote_order_id: order.remote_order_id.clone(),
            });
        }
    }

    if old.is_none_or(|value| value.equity != current.equity) {
        out.push(AccountBusinessChange::Equity {
            segment_key: current.segment_key.clone(),
            value: current.equity,
        });
    }
    if old
        .is_none_or(|value| value.status != current.status || value.freshness != current.freshness)
    {
        out.push(AccountBusinessChange::Status {
            segment_key: current.segment_key.clone(),
            status: current.status,
            stale: current.freshness == crate::application::AccountSegmentFreshness::Stale,
        });
    }
}

fn collect_event_keys(event: &AccountEvent, keys: &mut Vec<SegmentKey>, all_accounts: &mut bool) {
    match event {
        AccountEvent::Snapshot(snapshot) => keys.push(snapshot.segment_key.clone()),
        AccountEvent::EarnHoldings(snapshot) => keys.push(snapshot.segment_key.clone()),
        AccountEvent::Fill(fill) => keys.push(fill.segment_key.clone()),
        AccountEvent::ObservedFill(fill) => keys.push(fill.segment_key.clone()),
        AccountEvent::OrderObserved(_) => *all_accounts = true,
        AccountEvent::SimulatedCapitalMutation(mutation) => keys.push(mutation.segment_key.clone()),
        AccountEvent::Batch(events) => {
            for event in events {
                collect_event_keys(event, keys, all_accounts);
            }
        },
    }
}

fn earn_holding_key(holding: &crate::domain::EarnHolding) -> String {
    holding
        .participant_position_id
        .clone()
        .unwrap_or_else(|| holding.product_id.clone())
}

fn segment_selected(segments: &[String], key: &SegmentKey) -> bool {
    segments.is_empty() || segments.iter().any(|value| key == value)
}

fn compare_snapshot(
    account: &Account,
    snapshot: &AccountSnapshot,
) -> Vec<crate::application::AccountDifference> {
    let mut differences = Vec::new();
    let state = account.state();

    let external_balances: BTreeMap<_, _> = snapshot
        .balances
        .iter()
        .map(|value| (value.asset_id.clone(), value))
        .collect();
    if snapshot.kind == SnapshotKind::Delta {
        for (key, external) in external_balances {
            compare_balance(
                &mut differences,
                key.to_string(),
                state.balances().get(&key),
                Some(external),
            );
        }
    } else {
        let keys = state
            .balances()
            .keys()
            .chain(external_balances.keys())
            .cloned()
            .collect::<std::collections::BTreeSet<_>>();
        for key in keys {
            compare_balance(
                &mut differences,
                key.to_string(),
                state.balances().get(&key),
                external_balances.get(&key).copied(),
            );
        }
    }

    let external_positions: BTreeMap<_, _> = snapshot
        .positions
        .iter()
        .map(|value| ((value.instrument_id.clone(), value.position_side), value))
        .collect();
    let position_keys: Vec<_> = if snapshot.kind == SnapshotKind::Delta {
        external_positions.keys().cloned().collect()
    } else {
        state
            .positions()
            .keys()
            .chain(external_positions.keys())
            .cloned()
            .collect()
    };
    for key in position_keys {
        let local = state.positions().get(&key).map(|value| value.quantity);
        let external = external_positions.get(&key).map(|value| value.quantity);
        compare_decimal(
            &mut differences,
            "position.quantity",
            format!("{}:{}", key.0, key.1.as_str()),
            local,
            external,
        );
    }

    let external_orders: BTreeMap<_, _> = snapshot
        .open_orders
        .iter()
        .map(|value| (value.order_id.clone(), value))
        .collect();
    let order_keys: Vec<kairos_primitives::execution::OrderId> =
        if snapshot.kind == SnapshotKind::Delta {
            external_orders.keys().cloned().collect()
        } else {
            state
                .open_orders()
                .keys()
                .chain(external_orders.keys())
                .cloned()
                .collect()
        };
    for key in order_keys {
        let local = state.open_orders().get(&key).map(|value| {
            SignedQuantity::new(value.quantity.mantissa(), value.quantity.scale())
                .expect("validated order quantity")
        });
        let external = external_orders.get(&key).map(|value| {
            SignedQuantity::new(value.quantity.mantissa(), value.quantity.scale())
                .expect("validated order quantity")
        });
        if local.is_none() || external.is_none() {
            compare_decimal(
                &mut differences,
                "open_order.present",
                key.to_string(),
                local.map(|_| SignedQuantity::new(1, 0).expect("valid presence marker")),
                external.map(|_| SignedQuantity::new(1, 0).expect("valid presence marker")),
            );
        } else {
            compare_decimal(
                &mut differences,
                "open_order.quantity",
                key.to_string(),
                local,
                external,
            );
        }
    }
    differences
}

fn compare_balance(
    differences: &mut Vec<crate::application::AccountDifference>,
    key: String,
    local: Option<&Balance>,
    external: Option<&Balance>,
) {
    compare_decimal(
        differences,
        "balance.total",
        key.clone(),
        local.map(|value| value.total),
        external.map(|value| value.total),
    );
    compare_decimal(
        differences,
        "balance.available",
        key.clone(),
        local.and_then(|value| value.available),
        external.and_then(|value| value.available),
    );
    compare_decimal(
        differences,
        "balance.locked",
        key,
        local.and_then(|value| value.locked),
        external.and_then(|value| value.locked),
    );
}

fn compare_decimal(
    differences: &mut Vec<crate::application::AccountDifference>,
    field: &str,
    key: String,
    local: Option<SignedQuantity>,
    external: Option<SignedQuantity>,
) {
    let local = local.unwrap_or_default();
    let external = external.unwrap_or_default();
    let differs = local
        .cmp_value(external)
        .map(|ordering| ordering != std::cmp::Ordering::Equal)
        .unwrap_or(true);
    if differs {
        differences.push(crate::application::AccountDifference {
            field: field.into(),
            key,
            local,
            external,
        });
    }
}
