use std::collections::BTreeMap;

use crate::application::protocol::{AccountSnapshotSource, AccountStateStore, AccountStreamSource};
use crate::application::AccountsSnapshot;
use crate::application::{AccountRefreshIssue, AccountRefreshReport};
use crate::domain::{
    Account, AccountEvent, AccountSegment, AccountSnapshot, AccountView, Balance, DecimalValue,
};

pub struct AccountActor {
    actor_id: String,
    accounts: BTreeMap<String, Account>,
    source: Box<dyn AccountSnapshotSource>,
    store: Option<Box<dyn AccountStateStore>>,
    streams: Vec<Box<dyn AccountStreamSource>>,
    generation: u64,
    event_sequence: u64,
}

impl AccountActor {
    pub fn new(
        segments: Vec<AccountSegment>,
        source: Box<dyn AccountSnapshotSource>,
        mut store: Option<Box<dyn AccountStateStore>>,
    ) -> Result<Self, String> {
        let mut accounts = BTreeMap::new();
        for segment in segments {
            let key = segment.segment_key.clone();
            if accounts.contains_key(&key) {
                return Err(format!("duplicate account segment: {key}"));
            }
            accounts.insert(key, Account::new(segment)?);
        }
        if let Some(value) = store.as_mut() {
            for (segment, state) in value.load()? {
                let account = accounts.get_mut(&segment.segment_key).ok_or_else(|| {
                    format!("stored segment is not configured: {}", segment.segment_key)
                })?;
                account.state = state;
            }
        }
        Ok(Self {
            actor_id: "account".into(),
            accounts,
            source,
            store,
            streams: Vec::new(),
            generation: 0,
            event_sequence: 0,
        })
    }

    pub fn actor_id(&self) -> &str {
        &self.actor_id
    }

    pub fn plan_order(
        &mut self,
        request: crate::domain::OrderRequest,
        at: u64,
    ) -> Result<(), String> {
        let account = self
            .accounts
            .get_mut(&request.segment_key)
            .ok_or_else(|| format!("order segment is not configured: {}", request.segment_key))?;
        account.plan_order(request, at)?;
        self.persist()
    }

    pub fn apply_order_event(&mut self, event: crate::domain::OrderEvent) -> Result<(), String> {
        let account = self
            .accounts
            .values_mut()
            .find(|account| account.state.orders.contains_key(&event.order_id))
            .ok_or_else(|| format!("order is not configured: {}", event.order_id))?;
        account.apply_order_event(event)?;
        self.persist()
    }

    pub fn apply_fill(&mut self, fill: crate::domain::AccountFill) -> Result<(), String> {
        let account = self
            .accounts
            .get_mut(&fill.segment_key)
            .ok_or_else(|| format!("fill segment is not configured: {}", fill.segment_key))?;
        account.apply_fill(fill)?;
        self.event_sequence += 1;
        self.generation += 1;
        self.persist()
    }

    pub fn attach_stream(&mut self, stream: Box<dyn AccountStreamSource>) {
        self.streams.push(stream);
    }

    pub fn has_stream(&self) -> bool {
        !self.streams.is_empty()
    }

    pub fn poll_stream_once(&mut self) -> Result<bool, String> {
        if self.streams.is_empty() {
            return Err("account stream is not configured".into());
        }
        let mut event = None;
        for stream in &mut self.streams {
            if let Some(value) = stream.next_event()? {
                event = Some(value);
                break;
            }
        }
        let Some(event) = event else {
            return Ok(false);
        };
        match event {
            AccountEvent::Batch(events) => {
                for event in events {
                    self.apply_stream_event(event)?;
                }
            }
            event => self.apply_stream_event(event)?,
        }
        self.event_sequence += 1;
        self.generation += 1;
        self.persist().map(|()| true)
    }

    fn apply_stream_event(&mut self, event: AccountEvent) -> Result<(), String> {
        match event {
            AccountEvent::Snapshot(snapshot) => {
                let account = self
                    .accounts
                    .values_mut()
                    .find(|value| value.segment.segment_key == snapshot.segment_key)
                    .ok_or_else(|| {
                        format!("stream segment is not configured: {}", snapshot.segment_key)
                    })?;
                account.apply_snapshot(snapshot)?;
            }
            AccountEvent::Fill(fill) => {
                self.apply_fill(fill)?;
            }
            AccountEvent::Order(event) => {
                let account = self
                    .accounts
                    .values_mut()
                    .find(|account| account.state.orders.contains_key(&event.order_id))
                    .ok_or_else(|| format!("order is not configured: {}", event.order_id))?;
                account.apply_order_event(event)?;
            }
            AccountEvent::Batch(_) => {
                return Err("nested account event batch is not supported".into())
            }
        }
        Ok(())
    }

    pub fn refresh(&mut self, account_id: &str, segments: &[String]) -> Result<usize, String> {
        let report = self.refresh_report(account_id, segments)?;
        if let Some(issue) = report.issues.first() {
            return Err(format!(
                "account refresh failed for {} segment(s); first issue on {}: {}",
                report.issues.len(),
                issue.segment_key,
                issue.error
            ));
        }
        Ok(report.refreshed_segments.len())
    }

    pub fn refresh_report(
        &mut self,
        account_id: &str,
        segments: &[String],
    ) -> Result<AccountRefreshReport, String> {
        let keys: Vec<String> = self
            .accounts
            .iter()
            .filter(|(_, account)| {
                account.segment.identity.account_id == account_id
                    && (segments.is_empty() || segments.contains(&account.segment.segment_key))
            })
            .map(|(key, _)| key.clone())
            .collect();
        if keys.is_empty() {
            return Err(format!("no configured segments for account: {account_id}"));
        }
        let mut refreshed = Vec::new();
        let mut issues = Vec::new();
        let mut differences = Vec::new();
        for key in keys {
            let segment = self
                .accounts
                .get(&key)
                .expect("key collected")
                .segment
                .clone();
            let started = std::time::Instant::now();
            match self.source.fetch(&segment) {
                Ok(snapshot) => {
                    let previous = self.accounts.get(&key).expect("key collected");
                    differences.extend(compare_snapshot(previous, &snapshot));
                    match self
                        .accounts
                        .get_mut(&key)
                        .expect("key collected")
                        .apply_snapshot(snapshot)
                    {
                        Ok(()) => {
                            refreshed.push(key);
                            self.event_sequence += 1;
                        }
                        Err(error) => issues.push(refresh_issue(&key, error, started)),
                    }
                }
                Err(error) => issues.push(refresh_issue(&key, error, started)),
            }
        }
        if !refreshed.is_empty() {
            self.generation += 1;
            if let Some(store) = self.store.as_mut() {
                let values: Vec<_> = self.accounts.values().cloned().collect();
                store.save(&values)?;
            }
        }
        Ok(AccountRefreshReport {
            account_id: account_id.into(),
            refreshed_segments: refreshed,
            issues,
            differences,
        })
    }

    pub fn reconcile(&mut self, account_id: &str, segments: &[String]) -> Result<usize, String> {
        let report = self.reconcile_report(account_id, segments)?;
        if let Some(issue) = report.issues.first() {
            return Err(format!(
                "account reconciliation failed for {} segment(s); first issue on {}: {}",
                report.issues.len(),
                issue.segment_key,
                issue.error
            ));
        }
        Ok(report.refreshed_segments.len())
    }

    pub fn reconcile_report(
        &mut self,
        account_id: &str,
        segments: &[String],
    ) -> Result<AccountRefreshReport, String> {
        let keys: Vec<String> = self
            .accounts
            .iter()
            .filter(|(_, account)| {
                account.segment.identity.account_id == account_id
                    && (segments.is_empty() || segments.contains(&account.segment.segment_key))
            })
            .map(|(key, _)| key.clone())
            .collect();
        if keys.is_empty() {
            return Err(format!("no configured segments for account: {account_id}"));
        }
        for key in &keys {
            self.accounts
                .get_mut(key)
                .expect("key collected")
                .state
                .status = crate::domain::AccountStatus::Reconciling;
        }
        self.persist()?;
        self.refresh_report(account_id, segments)
    }

    fn persist(&mut self) -> Result<(), String> {
        if let Some(store) = self.store.as_mut() {
            let values: Vec<_> = self.accounts.values().cloned().collect();
            store.save(&values)?;
        }
        Ok(())
    }

    pub fn query(&self, account_id: &str, segments: &[String]) -> Vec<AccountView> {
        self.accounts
            .values()
            .filter(|account| {
                account.segment.identity.account_id == account_id
                    && (segments.is_empty() || segments.contains(&account.segment.segment_key))
            })
            .cloned()
            .map(|account| AccountView { account })
            .collect()
    }

    pub fn snapshot(&self) -> AccountsSnapshot {
        AccountsSnapshot {
            actor_id: self.actor_id.clone(),
            generation: self.generation,
            event_sequence: self.event_sequence,
            accounts: self
                .accounts
                .values()
                .cloned()
                .map(|account| AccountView { account })
                .collect(),
        }
    }
}

fn refresh_issue(
    segment_key: &str,
    error: String,
    started: std::time::Instant,
) -> AccountRefreshIssue {
    let elapsed_ms = started.elapsed().as_millis() as u64;
    AccountRefreshIssue {
        segment_key: segment_key.into(),
        error,
        elapsed_ms,
        diagnostic_id: format!("account-refresh-{segment_key}-{elapsed_ms}"),
    }
}

fn compare_snapshot(
    account: &Account,
    snapshot: &AccountSnapshot,
) -> Vec<crate::application::AccountDifference> {
    let mut differences = Vec::new();
    let state = &account.state;

    let external_balances: BTreeMap<_, _> = snapshot
        .balances
        .iter()
        .map(|value| (value.asset_id.clone(), value))
        .collect();
    if snapshot.partial {
        for (key, external) in external_balances {
            compare_balance(
                &mut differences,
                key.clone(),
                state.balances.get(&key),
                Some(external),
            );
        }
    } else {
        let keys = state
            .balances
            .keys()
            .chain(external_balances.keys())
            .cloned()
            .collect::<std::collections::BTreeSet<_>>();
        for key in keys {
            compare_balance(
                &mut differences,
                key.clone(),
                state.balances.get(&key),
                external_balances.get(&key).copied(),
            );
        }
    }

    let external_positions: BTreeMap<_, _> = snapshot
        .positions
        .iter()
        .map(|value| (value.instrument_id.clone(), value))
        .collect();
    let position_keys: Vec<String> = if snapshot.partial {
        external_positions.keys().cloned().collect()
    } else {
        state
            .positions
            .keys()
            .chain(external_positions.keys())
            .cloned()
            .collect()
    };
    for key in position_keys {
        let local = state.positions.get(&key).map(|value| value.quantity);
        let external = external_positions.get(&key).map(|value| value.quantity);
        compare_decimal(&mut differences, "position.quantity", key, local, external);
    }

    let external_orders: BTreeMap<_, _> = snapshot
        .open_orders
        .iter()
        .map(|value| (value.order_id.clone(), value))
        .collect();
    let order_keys: Vec<String> = if snapshot.partial {
        external_orders.keys().cloned().collect()
    } else {
        state
            .open_orders
            .keys()
            .chain(external_orders.keys())
            .cloned()
            .collect()
    };
    for key in order_keys {
        let local = state.open_orders.get(&key).map(|value| value.quantity);
        let external = external_orders.get(&key).map(|value| value.quantity);
        if local.is_none() || external.is_none() {
            compare_decimal(
                &mut differences,
                "open_order.present",
                key,
                local.map(|_| DecimalValue::new(1, 0)),
                external.map(|_| DecimalValue::new(1, 0)),
            );
        } else {
            compare_decimal(
                &mut differences,
                "open_order.quantity",
                key,
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
    local: Option<DecimalValue>,
    external: Option<DecimalValue>,
) {
    let local = local.unwrap_or_default();
    let external = external.unwrap_or_default();
    if (decimal_value(local) - decimal_value(external)).abs() > 0.00000001 {
        differences.push(crate::application::AccountDifference {
            field: field.into(),
            key,
            local,
            external,
        });
    }
}

fn decimal_value(value: DecimalValue) -> f64 {
    value.mantissa as f64 / 10_f64.powi(value.scale as i32)
}
