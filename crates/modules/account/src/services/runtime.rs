use std::collections::VecDeque;
use std::sync::Arc;
use std::sync::mpsc::Receiver;

use kairos_primitives::runtime::ActorId;
use tracing::info;

use crate::application::{
    AccountCurrentView, AccountRefreshIssue, AccountRefreshReport, MarkToMarket,
};
use crate::domain::{
    AccountEvent, AccountFill, AccountSegment, AccountSnapshot, ApplyOutcome, Money, Position,
    SegmentKey, SignedQuantity, SnapshotKind,
};
use crate::services::actor::AccountActor;
use crate::services::persistence::{AccountJournalRecord, JsonAccountStore};
use crate::services::persistence_worker::AccountPersistenceWorker;
use crate::services::refresh::{AccountRefreshWorker, RefreshFetch, try_receive};

/// Drives the state-only actor through concrete IO selected by composition.
/// It owns no account facts; all business mutation remains inside `AccountActor`.
pub(crate) struct AccountRuntime {
    actor: AccountActor,
    cached_current_view: Arc<AccountCurrentView>,
    refresh_worker: Option<AccountRefreshWorker>,
    pending_refresh: Option<(String, Receiver<Vec<RefreshFetch>>)>,
    persistence: Option<AccountPersistenceWorker>,
    journal_events_since_checkpoint: usize,
    pending_business_events: VecDeque<crate::application::AccountBusinessEvent>,
}

impl AccountRuntime {
    pub(crate) fn new(
        segments: Vec<AccountSegment>,
        source: Option<crate::services::integration::AccountSnapshotGateway>,
        mut store: Option<JsonAccountStore>,
    ) -> Result<Self, String> {
        let restored = match store.as_mut() {
            Some(store) => store.load()?,
            None => crate::services::persistence::PersistedAccounts {
                schema_version: 1,
                actor_id: ActorId::new("account").expect("valid account actor ID"),
                generation: 0.into(),
                event_sequence: 0.into(),
                accounts: Vec::new(),
                pending_business_events: Vec::new(),
            },
        };
        let restored_count = restored.accounts.len();
        let mut pending_business_events = VecDeque::from(restored.pending_business_events);
        let mut actor = AccountActor::new(
            segments,
            restored.accounts,
            restored.generation,
            restored.event_sequence,
        )?;
        let journal_events_since_checkpoint = if let Some(store) = store.as_ref() {
            let records = store.load_journal()?;
            let count = records.len();
            for record in records {
                match record {
                    AccountJournalRecord::Transition {
                        events,
                        business_events,
                    } => {
                        for event in events {
                            actor.apply_events(event)?;
                        }
                        pending_business_events.extend(business_events);
                    },
                    AccountJournalRecord::PublicationAcknowledged {
                        sequence,
                        account_id,
                    } => acknowledge_outbox_event(
                        &mut pending_business_events,
                        sequence,
                        &account_id,
                    ),
                }
            }
            count
        } else {
            0
        };
        let cached_current_view = actor.current_view();
        if restored_count > 0 {
            info!(
                event = "account_state_restored",
                component = "account",
                segment_count = restored_count,
                "account state restored from persistence"
            );
        }
        Ok(Self {
            actor,
            cached_current_view: Arc::new(cached_current_view),
            refresh_worker: source.map(AccountRefreshWorker::new),
            pending_refresh: None,
            persistence: store.map(AccountPersistenceWorker::new),
            journal_events_since_checkpoint,
            pending_business_events,
        })
    }

    pub(crate) fn has_refresh_worker(&self) -> bool {
        self.refresh_worker.is_some()
    }

    pub(crate) fn apply_simulated_fill(
        &mut self,
        fill: AccountFill,
    ) -> Result<ApplyOutcome, String> {
        let actor_before = self.actor.clone();
        let projection = self
            .actor
            .projection(&fill.segment_key)
            .ok_or_else(|| format!("fill segment is not configured: {}", fill.segment_key))?;
        let settlement = crate::services::settlement::settle_paper_fill(&projection, &fill)
            .map_err(|error| error.to_string())?;
        let fill_event = AccountEvent::Fill(fill.clone());
        let undo = self
            .actor
            .undo_for_events(std::slice::from_ref(&fill_event));
        let fill_outcome = self.actor.record_fill(fill)?;
        if fill_outcome != ApplyOutcome::Applied {
            return Ok(fill_outcome);
        }
        let settlement_event = AccountEvent::Snapshot(settlement.clone());
        let (settlement_outcome, _) = match self.actor.apply_snapshot(settlement) {
            Ok(value) => value,
            Err(error) => {
                self.actor.restore_undo(undo);
                return Err(error);
            },
        };
        if settlement_outcome != ApplyOutcome::Applied {
            self.actor.restore_undo(undo);
            return Ok(settlement_outcome);
        }
        let events = [fill_event, settlement_event];
        let business_events = self.actor.business_events_since(&actor_before);
        if let Err(error) = self.persist_transition(&events, &business_events) {
            self.actor.restore_undo(undo);
            return Err(error);
        }
        self.cached_current_view = Arc::new(self.actor.current_view());
        self.pending_business_events.extend(business_events);
        self.maybe_checkpoint()?;
        Ok(ApplyOutcome::Applied)
    }

    pub(crate) fn apply_simulated_capital_mutation(
        &mut self,
        mutation: crate::domain::SimulatedCapitalMutation,
    ) -> Result<ApplyOutcome, String> {
        let applied = self.apply_event(AccountEvent::SimulatedCapitalMutation(mutation))?;
        Ok(if applied == 0 {
            ApplyOutcome::Duplicate
        } else {
            ApplyOutcome::Applied
        })
    }

    pub(crate) fn simulated_capital_mutation_applied(
        &self,
        segment_key: &crate::domain::SegmentKey,
        mutation_id: &kairos_primitives::runtime::IdempotencyKey,
    ) -> Result<bool, String> {
        self.actor
            .has_simulated_capital_mutation(segment_key, mutation_id)
    }

    pub(crate) fn mark_to_market(&mut self, request: MarkToMarket) -> Result<(), String> {
        let segment_key = request.segment_key.clone();
        let projection = self
            .actor
            .projection(&segment_key)
            .ok_or_else(|| format!("mark segment is not configured: {}", request.segment_key))?;
        let mut positions = projection.positions.clone();
        let mut found = false;
        for position in &mut positions {
            if position.instrument_id == request.instrument_id {
                position.mark_price = Some(request.mark_price);
                position.unrealized_pnl = Some(unrealized_pnl(position)?);
                position.updated_at_unix_nanos = request.observed_at_unix_nanos;
                found = true;
            }
        }
        if !found {
            return Err(format!(
                "mark instrument is not present in account: {}",
                request.instrument_id
            ));
        }
        let equity = calculate_equity(&projection, &positions, &request.quote_asset)?;
        let initial_equity = projection.initial_equity.or(Some(equity));
        let net_profit = initial_equity
            .map(|initial| {
                equity
                    .checked_sub(initial)
                    .map_err(|error| error.to_string())
            })
            .transpose()?;
        let snapshot = AccountSnapshot {
            segment_key,
            balances: Vec::new(),
            collateral: Vec::new(),
            positions,
            open_orders: Vec::new(),
            status: projection.status,
            observed_at_unix_nanos: request.observed_at_unix_nanos,
            equity: Some(equity),
            initial_equity,
            net_profit,
            account_model: projection.observed_account_model,
            margin_mode: projection.margin_mode,
            position_mode: projection.position_mode,
            kind: SnapshotKind::Delta,
        };
        self.apply_event(AccountEvent::Snapshot(snapshot))?;
        Ok(())
    }

    pub(crate) fn apply_event(&mut self, event: AccountEvent) -> Result<usize, String> {
        self.apply_event_with_provenance(event, None)
    }

    pub(crate) fn apply_event_with_provenance(
        &mut self,
        event: AccountEvent,
        provenance: Option<crate::application::AccountFactProvenance>,
    ) -> Result<usize, String> {
        let actor_before = self.actor.clone();
        let events = match event {
            AccountEvent::Batch(events) => events,
            event => vec![event],
        };
        let undo = self.actor.undo_for_events(&events);
        let mut applied = 0;
        for event in events.iter().cloned() {
            match self.actor.apply_events(event) {
                Ok(value) => applied += value as usize,
                Err(error) => {
                    self.actor.restore_undo(undo);
                    return Err(error);
                },
            }
        }
        if applied == 0 {
            return Ok(0);
        }
        let mut business_events = self.actor.business_events_since(&actor_before);
        for event in &mut business_events {
            event.provenance = provenance.clone();
        }
        if let Err(error) = self.persist_transition(&events, &business_events) {
            self.actor.restore_undo(undo);
            return Err(error);
        }
        self.cached_current_view = Arc::new(self.actor.current_view());
        self.pending_business_events.extend(business_events);
        self.maybe_checkpoint()?;
        Ok(applied)
    }

    pub(crate) fn refresh(
        &mut self,
        account_id: &str,
        segments: &[String],
    ) -> Result<usize, String> {
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

    pub(crate) fn refresh_report(
        &mut self,
        account_id: &str,
        segments: &[String],
    ) -> Result<AccountRefreshReport, String> {
        if self.pending_refresh.is_some() {
            return Err("account refresh is already pending".into());
        }
        let selected = self.actor.selected_segments(account_id, segments)?;
        let receiver = self
            .refresh_worker
            .as_ref()
            .ok_or_else(|| "synchronous account refresh source is not configured".to_string())?
            .submit(selected)?;
        let fetches = receiver
            .recv()
            .map_err(|_| "account refresh worker stopped".to_string())?;
        self.apply_refresh_fetches(account_id, fetches)
    }

    pub(crate) fn start_refresh(
        &mut self,
        account_id: &str,
        segments: &[String],
    ) -> Result<(), String> {
        if self.pending_refresh.is_some() {
            return Ok(());
        }
        let selected = self.actor.selected_segments(account_id, segments)?;
        let receiver = self
            .refresh_worker
            .as_ref()
            .ok_or_else(|| "synchronous account refresh source is not configured".to_string())?
            .submit(selected)?;
        self.pending_refresh = Some((account_id.to_string(), receiver));
        Ok(())
    }

    pub(crate) fn poll_refresh(&mut self) -> Result<Option<AccountRefreshReport>, String> {
        let Some((account_id, receiver)) = self.pending_refresh.as_ref() else {
            return Ok(None);
        };
        let Some(fetches) = try_receive(receiver)? else {
            return Ok(None);
        };
        let account_id = account_id.clone();
        self.pending_refresh = None;
        self.apply_refresh_fetches(&account_id, fetches).map(Some)
    }

    pub(crate) fn refresh_pending(&self) -> bool {
        self.pending_refresh.is_some()
    }

    pub(crate) fn selected_segments(
        &self,
        account_id: &str,
        segments: &[String],
    ) -> Result<Vec<AccountSegment>, String> {
        self.actor.selected_segments(account_id, segments)
    }

    pub(crate) fn apply_refresh_fetches(
        &mut self,
        account_id: &str,
        fetches: Vec<RefreshFetch>,
    ) -> Result<AccountRefreshReport, String> {
        let mut candidate = self.actor.clone();
        let mut refreshed = Vec::new();
        let mut issues = Vec::new();
        let mut differences = Vec::new();
        for fetch in fetches {
            let segment = fetch.segment;
            let key = segment.segment_key.clone();
            match fetch.result {
                Ok(snapshot) => match candidate.apply_snapshot(snapshot) {
                    Ok((ApplyOutcome::Applied, observed_differences)) => {
                        refreshed.push(key);
                        differences.extend(observed_differences);
                    },
                    Ok((_, observed_differences)) => differences.extend(observed_differences),
                    Err(error) => issues.push(refresh_issue(&key, error, fetch.elapsed_ms)),
                },
                Err(error) => issues.push(refresh_issue(&key, error, fetch.elapsed_ms)),
            }
        }
        if !refreshed.is_empty() {
            self.commit_candidate(candidate)?;
        }
        Ok(AccountRefreshReport {
            account_id: kairos_primitives::account::AccountId::new(account_id)
                .map_err(|error| error.to_string())?,
            refreshed_segments: refreshed,
            issues,
            differences,
        })
    }

    pub(crate) fn reconcile(
        &mut self,
        account_id: &str,
        segments: &[String],
    ) -> Result<usize, String> {
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

    pub(crate) fn reconcile_report(
        &mut self,
        account_id: &str,
        segments: &[String],
    ) -> Result<AccountRefreshReport, String> {
        let mut candidate = self.actor.clone();
        if candidate.begin_reconciliation(account_id, segments)? {
            self.commit_candidate(candidate)?;
        }
        self.refresh_report(account_id, segments)
    }

    pub(crate) fn current_view(&self) -> AccountCurrentView {
        (*self.cached_current_view).clone()
    }

    pub(crate) fn current_view_shared(&self) -> Arc<AccountCurrentView> {
        Arc::clone(&self.cached_current_view)
    }

    pub(crate) fn generation(&self) -> u64 {
        self.actor.persistence_metadata().1.get()
    }

    pub(crate) fn event_sequence(&self) -> u64 {
        self.actor.persistence_metadata().2.get()
    }

    pub(crate) fn actor_id(&self) -> &str {
        self.actor.persistence_metadata().0
    }

    pub(crate) fn pending_business_event(
        &self,
    ) -> Option<&crate::application::AccountBusinessEvent> {
        self.pending_business_events.front()
    }

    pub(crate) fn acknowledge_business_event(&mut self) -> Result<(), String> {
        let Some(event) = self.pending_business_events.front() else {
            return Ok(());
        };
        if let Some(persistence) = self.persistence.as_ref() {
            persistence.append_journal(vec![AccountJournalRecord::PublicationAcknowledged {
                sequence: event.sequence,
                account_id: event.account_id.clone(),
            }])?;
            self.journal_events_since_checkpoint += 1;
        }
        self.pending_business_events.pop_front();
        Ok(())
    }

    fn persist_candidate(
        &self,
        candidate: &AccountActor,
        pending_business_events: &VecDeque<crate::application::AccountBusinessEvent>,
    ) -> Result<(), String> {
        if let Some(persistence) = self.persistence.as_ref() {
            let (actor_id, generation, event_sequence) = candidate.persistence_metadata();
            persistence.checkpoint(
                actor_id.to_string(),
                generation.get(),
                event_sequence.get(),
                candidate.persistent_accounts(),
                pending_business_events.iter().cloned().collect(),
            )?;
        }
        Ok(())
    }

    fn commit_candidate(&mut self, candidate: AccountActor) -> Result<(), String> {
        let business_events = candidate.business_events_since(&self.actor);
        let mut pending_business_events = self.pending_business_events.clone();
        pending_business_events.extend(business_events.iter().cloned());
        self.persist_candidate(&candidate, &pending_business_events)?;
        self.journal_events_since_checkpoint = 0;
        self.cached_current_view = Arc::new(candidate.current_view());
        self.actor = candidate;
        self.pending_business_events.extend(business_events);
        Ok(())
    }

    fn persist_transition(
        &mut self,
        events: &[AccountEvent],
        business_events: &[crate::application::AccountBusinessEvent],
    ) -> Result<(), String> {
        if let Some(persistence) = self.persistence.as_ref() {
            let record = AccountJournalRecord::Transition {
                events: events.to_vec(),
                business_events: business_events.to_vec(),
            };
            if !business_events.is_empty() || events.iter().any(event_requires_durability) {
                persistence.append_journal(vec![record])?;
            } else {
                persistence.enqueue_journal(vec![record])?;
            }
            self.journal_events_since_checkpoint += 1;
        }
        Ok(())
    }

    fn maybe_checkpoint(&mut self) -> Result<(), String> {
        if self.journal_events_since_checkpoint >= 1_024 {
            self.persist_candidate(&self.actor, &self.pending_business_events)?;
            self.journal_events_since_checkpoint = 0;
        }
        Ok(())
    }
}

fn acknowledge_outbox_event(
    events: &mut VecDeque<crate::application::AccountBusinessEvent>,
    sequence: kairos_primitives::time::Sequence,
    account_id: &kairos_primitives::account::AccountId,
) {
    if let Some(position) = events
        .iter()
        .position(|event| event.sequence == sequence && &event.account_id == account_id)
    {
        events.remove(position);
    }
}

fn unrealized_pnl(position: &Position) -> Result<Money, String> {
    let Some(average_price) = position.average_price else {
        return Ok(Money::ZERO);
    };
    let Some(mark_price) = position.mark_price else {
        return Ok(Money::ZERO);
    };
    mark_price
        .checked_sub(average_price)
        .and_then(|delta| delta.checked_mul(position.quantity))
        .map_err(|error| error.to_string())
}

fn calculate_equity(
    projection: &crate::application::AccountSegmentView,
    positions: &[Position],
    quote_asset: &str,
) -> Result<Money, String> {
    let balance = projection
        .balances
        .iter()
        .find(|value| value.asset_code.eq_ignore_ascii_case(quote_asset))
        .map(|value| value.total)
        .unwrap_or(SignedQuantity::ZERO);
    let mut equity =
        Money::new(balance.mantissa(), balance.scale()).map_err(|error| error.to_string())?;
    for position in positions {
        if let Some(mark_price) = position.mark_price {
            equity = equity
                .checked_add(
                    position
                        .quantity
                        .checked_mul(mark_price)
                        .map_err(|error| error.to_string())?,
                )
                .map_err(|error| error.to_string())?;
        }
    }
    Ok(equity)
}

fn event_requires_durability(event: &AccountEvent) -> bool {
    match event {
        AccountEvent::Fill(_)
        | AccountEvent::ObservedFill(_)
        | AccountEvent::SimulatedCapitalMutation(_) => true,
        AccountEvent::Batch(events) => events.iter().any(event_requires_durability),
        AccountEvent::Snapshot(_)
        | AccountEvent::EarnHoldings(_)
        | AccountEvent::OrderObserved(_) => false,
    }
}

fn refresh_issue(segment_key: &SegmentKey, error: String, elapsed_ms: u64) -> AccountRefreshIssue {
    AccountRefreshIssue {
        segment_key: segment_key.clone(),
        error,
        elapsed_ms,
        diagnostic_id: format!("account-refresh-{segment_key}-{elapsed_ms}"),
    }
}
