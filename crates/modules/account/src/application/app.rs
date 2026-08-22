use std::sync::Arc;
use std::time::Instant;

use tracing::{info, warn};

use super::{
    AccountCurrentView, AccountError, AccountRefreshReport, MarkToMarket, ReconcileAccount,
    RefreshAccount,
};
use crate::domain::{AccountEvent, AccountObservedFill, AccountSegment};
use crate::services::integration::AccountSnapshotGateway;
use crate::services::persistence::JsonAccountStore;
use crate::services::runtime::AccountRuntime;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum AccountRuntimeMode {
    #[default]
    Live,
    Simulation,
}

pub struct AccountApplication {
    runtime: AccountRuntime,
    runtime_mode: AccountRuntimeMode,
    business_time_unix_nanos: Option<u64>,
    pub(super) conflux: super::conflux::AccountConfluxState,
}

impl AccountApplication {
    pub(crate) fn new(runtime: AccountRuntime) -> Self {
        Self {
            runtime,
            runtime_mode: AccountRuntimeMode::Live,
            business_time_unix_nanos: None,
            conflux: super::conflux::AccountConfluxState::default(),
        }
    }

    pub(crate) fn enable_simulation(&mut self) {
        self.runtime_mode = AccountRuntimeMode::Simulation;
    }

    pub const fn runtime_mode(&self) -> AccountRuntimeMode {
        self.runtime_mode
    }

    pub const fn simulation_commands_enabled(&self) -> bool {
        matches!(self.runtime_mode, AccountRuntimeMode::Simulation)
    }

    pub const fn business_time_unix_nanos(&self) -> Option<u64> {
        self.business_time_unix_nanos
    }

    pub fn advance_business_time(
        &mut self,
        event_time_unix_nanos: u64,
    ) -> Result<(), AccountError> {
        if !self.simulation_commands_enabled() {
            return Err(AccountError::Invalid(
                "simulation command is disabled for this Account application".into(),
            ));
        }
        if self
            .business_time_unix_nanos
            .is_some_and(|current| event_time_unix_nanos < current)
        {
            return Err(AccountError::Invalid(
                "account business time cannot move backwards".into(),
            ));
        }
        self.business_time_unix_nanos = Some(event_time_unix_nanos);
        Ok(())
    }

    pub fn generation(&self) -> u64 {
        self.runtime.generation()
    }

    pub(crate) fn has_refresh_worker(&self) -> bool {
        self.runtime.has_refresh_worker()
    }

    pub fn event_sequence(&self) -> u64 {
        self.runtime.event_sequence()
    }

    pub(crate) fn pending_business_event(&self) -> Option<&super::AccountBusinessEvent> {
        self.runtime.pending_business_event()
    }

    pub(crate) fn acknowledge_business_event(&mut self) -> Result<(), AccountError> {
        self.runtime
            .acknowledge_business_event()
            .map_err(AccountError::Source)
    }

    pub(crate) fn apply_event_with_provenance(
        &mut self,
        event: AccountEvent,
        provenance: super::AccountFactProvenance,
    ) -> Result<usize, AccountError> {
        self.runtime
            .apply_event_with_provenance(event, Some(provenance))
            .map_err(AccountError::Source)
    }

    pub fn actor_id(&self) -> &str {
        self.runtime.actor_id()
    }

    pub fn refresh(&mut self, request: RefreshAccount) -> Result<usize, AccountError> {
        if request.account_id.as_str().trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        let segments = request
            .segments
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        self.runtime
            .refresh(request.account_id.as_str(), &segments)
            .map_err(AccountError::Source)
    }

    pub fn refresh_report(
        &mut self,
        request: RefreshAccount,
    ) -> Result<AccountRefreshReport, AccountError> {
        if request.account_id.as_str().trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        let started = Instant::now();
        info!(event = "account_refresh_started", component = "account", account_id = %request.account_id, requested_segments = request.segments.len(), "account refresh started");
        let segments = request
            .segments
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        match self
            .runtime
            .refresh_report(request.account_id.as_str(), &segments)
            .map_err(AccountError::Source)
        {
            Ok(report) => {
                if report.issues.is_empty() {
                    info!(event = "account_refresh_completed", component = "account", account_id = %report.account_id, refreshed_segments = report.refreshed_segments.len(), differences = report.differences.len(), duration_ms = started.elapsed().as_millis(), "account refresh completed");
                } else {
                    warn!(event = "account_refresh_degraded", component = "account", account_id = %report.account_id, refreshed_segments = report.refreshed_segments.len(), issues = report.issues.len(), differences = report.differences.len(), duration_ms = started.elapsed().as_millis(), "account refresh completed with issues");
                }
                Ok(report)
            },
            Err(error) => {
                warn!(event = "account_refresh_failed", component = "account", account_id = %request.account_id, duration_ms = started.elapsed().as_millis(), error = %error, "account refresh failed");
                Err(error)
            },
        }
    }

    pub fn start_refresh(&mut self, request: RefreshAccount) -> Result<(), AccountError> {
        if request.account_id.as_str().trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        let segments = request
            .segments
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        self.runtime
            .start_refresh(request.account_id.as_str(), &segments)
            .map_err(AccountError::Source)
    }

    pub fn poll_refresh(&mut self) -> Result<Option<AccountRefreshReport>, AccountError> {
        self.runtime.poll_refresh().map_err(AccountError::Source)
    }

    pub fn refresh_pending(&self) -> bool {
        self.runtime.refresh_pending()
    }

    pub fn reconcile(&mut self, request: ReconcileAccount) -> Result<usize, AccountError> {
        if request.account_id.as_str().trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        let segments = request
            .segments
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        self.runtime
            .reconcile(request.account_id.as_str(), &segments)
            .map_err(AccountError::Source)
    }

    pub fn reconcile_report(
        &mut self,
        request: ReconcileAccount,
    ) -> Result<AccountRefreshReport, AccountError> {
        if request.account_id.as_str().trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        let segments = request
            .segments
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        self.runtime
            .reconcile_report(request.account_id.as_str(), &segments)
            .map_err(AccountError::Source)
    }

    pub fn publish_current(
        &self,
        mut publish: impl FnMut(&AccountCurrentView) -> Result<(), String>,
    ) -> Result<(), AccountError> {
        publish(&self.runtime.current_view()).map_err(AccountError::Source)
    }

    pub(crate) fn current_view_shared(&self) -> Arc<AccountCurrentView> {
        self.runtime.current_view_shared()
    }

    pub fn apply_simulated_fill(
        &mut self,
        fill: crate::domain::AccountFill,
    ) -> Result<(), AccountError> {
        if !self.simulation_commands_enabled() {
            return Err(AccountError::Invalid(
                "simulation command is disabled for this Account application".into(),
            ));
        }
        info!(event = "account_fill_started", component = "account", fill_id = ?fill.fill_id, order_id = ?fill.order_id, segment = %fill.segment_key, "applying account fill");
        match self
            .runtime
            .apply_simulated_fill(fill)
            .map_err(AccountError::Invalid)
        {
            Ok(_) => {
                info!(
                    event = "account_fill_applied",
                    component = "account",
                    "account fill applied"
                );
                Ok(())
            },
            Err(error) => {
                warn!(event = "account_fill_rejected", component = "account", error = %error, "account fill rejected");
                Err(error)
            },
        }
    }

    pub fn apply_simulated_capital_mutation(
        &mut self,
        mutation: crate::domain::SimulatedCapitalMutation,
    ) -> Result<(), AccountError> {
        if !self.simulation_commands_enabled() {
            return Err(AccountError::Invalid(
                "simulation command is disabled for this Account application".into(),
            ));
        }
        self.runtime
            .apply_simulated_capital_mutation(mutation)
            .map(|_| ())
            .map_err(AccountError::Invalid)
    }

    pub fn simulated_capital_mutation_applied(
        &self,
        segment_key: &crate::domain::SegmentKey,
        mutation_id: &kairos_primitives::runtime::IdempotencyKey,
    ) -> Result<bool, AccountError> {
        if !self.simulation_commands_enabled() {
            return Err(AccountError::Invalid(
                "simulation command is disabled for this Account application".into(),
            ));
        }
        self.runtime
            .simulated_capital_mutation_applied(segment_key, mutation_id)
            .map_err(AccountError::Invalid)
    }

    pub fn mark_to_market(&mut self, request: MarkToMarket) -> Result<(), AccountError> {
        if !self.simulation_commands_enabled() {
            return Err(AccountError::Invalid(
                "simulation command is disabled for this Account application".into(),
            ));
        }
        if request.segment_key.trim().is_empty() {
            return Err(AccountError::Invalid("segment_key is required".into()));
        }
        if request.instrument_id.trim().is_empty() {
            return Err(AccountError::Invalid("instrument_id is required".into()));
        }
        if request.quote_asset.trim().is_empty() {
            return Err(AccountError::Invalid("quote_asset is required".into()));
        }
        if !request.mark_price.is_positive() {
            return Err(AccountError::Invalid("mark_price must be positive".into()));
        }
        self.runtime
            .mark_to_market(request)
            .map_err(AccountError::Invalid)
    }

    /// Apply an externally observed account fact. Live fills and order
    /// observations do not invoke paper settlement; balances and positions
    /// remain authoritative from provider snapshots/events.
    pub fn apply_event(&mut self, event: AccountEvent) -> Result<usize, AccountError> {
        self.runtime
            .apply_event(event)
            .map_err(AccountError::Invalid)
    }

    /// Record a private-stream fill that arrived before Execution confirmed
    /// the corresponding exchange order. This deliberately enters account
    /// reconciliation and does not settle balances or positions.
    pub fn observe_fill(&mut self, fill: AccountObservedFill) -> Result<usize, AccountError> {
        self.apply_event(AccountEvent::ObservedFill(fill))
    }

    pub(crate) fn with_dependencies(
        segments: Vec<AccountSegment>,
        source: AccountSnapshotGateway,
        store: Option<JsonAccountStore>,
    ) -> Result<Self, AccountError> {
        AccountRuntime::new(segments, Some(source), store)
            .map(Self::new)
            .map_err(AccountError::Invalid)
    }

    pub(crate) fn with_async_dependencies(
        segments: Vec<AccountSegment>,
        store: Option<JsonAccountStore>,
    ) -> Result<Self, AccountError> {
        AccountRuntime::new(segments, None, store)
            .map(Self::new)
            .map_err(AccountError::Invalid)
    }

    pub(crate) fn selected_refresh_segments(
        &self,
        request: &RefreshAccount,
    ) -> Result<Vec<AccountSegment>, AccountError> {
        let segments = request
            .segments
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        self.runtime
            .selected_segments(request.account_id.as_str(), &segments)
            .map_err(AccountError::Source)
    }

    pub(crate) fn apply_refresh_fetches(
        &mut self,
        account_id: &str,
        fetches: Vec<crate::services::refresh::RefreshFetch>,
    ) -> Result<AccountRefreshReport, AccountError> {
        self.runtime
            .apply_refresh_fetches(account_id, fetches)
            .map_err(AccountError::Source)
    }
}
