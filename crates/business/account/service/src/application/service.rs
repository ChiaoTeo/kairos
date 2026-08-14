use super::{
    AccountBalanceRow, AccountCapability, AccountDataQuery, AccountError, AccountFeeSchedule,
    AccountMarketProfile, AccountMarketProfileRequest, AccountProjection, AccountQuery,
    AccountRefreshReport, AccountsSnapshot, MarkToMarket, ReconcileAccount, RefreshAccount,
};
use crate::domain::{AccountEvent, AccountObservedFill, AccountSegment};
use crate::services::integration::{
    AccountAsyncMarketProfileGateway, AccountAsyncSnapshotGateway, AccountMarketProfileGateway,
    AccountSnapshotGateway,
};
use crate::services::persistence::JsonAccountStore;
use crate::services::runtime::AccountRuntime;
use std::sync::Arc;
use std::time::Instant;
use tracing::{info, warn};

type BalanceRows = Vec<(String, String, Vec<crate::domain::Balance>)>;

pub struct AccountApplication {
    runtime: AccountRuntime,
    market_profiles:
        std::collections::BTreeMap<(crate::domain::SegmentKey, String), AccountMarketProfile>,
    profile_source: Option<AccountMarketProfileGateway>,
    async_snapshot_source: Option<AccountAsyncSnapshotGateway>,
    async_profile_source: Option<AccountAsyncMarketProfileGateway>,
    trade_enabled: bool,
}

impl AccountApplication {
    pub(crate) fn new(runtime: AccountRuntime) -> Self {
        Self {
            runtime,
            market_profiles: std::collections::BTreeMap::new(),
            profile_source: None,
            async_snapshot_source: None,
            async_profile_source: None,
            trade_enabled: true,
        }
    }

    pub fn generation(&self) -> u64 {
        self.runtime.generation()
    }

    pub(crate) fn take_persistence_error(&self) -> Option<String> {
        self.runtime.take_persistence_error()
    }

    pub(crate) fn has_refresh_worker(&self) -> bool {
        self.runtime.has_refresh_worker()
    }

    pub(crate) fn persistence_queue_depth(&self) -> usize {
        self.runtime.persistence_queue_depth()
    }

    pub fn event_sequence(&self) -> u64 {
        self.runtime.event_sequence()
    }

    pub(crate) fn pending_business_event(&self) -> Option<&super::AccountBusinessEvent> {
        self.runtime.pending_business_event()
    }

    pub(crate) fn acknowledge_business_event(&mut self) {
        self.runtime.acknowledge_business_event();
    }

    pub(crate) fn attach_business_event_provenance(
        &mut self,
        provenance: super::AccountFactProvenance,
    ) {
        self.runtime.attach_business_event_provenance(provenance);
    }

    pub fn actor_id(&self) -> &str {
        self.runtime.actor_id()
    }

    pub fn query(&self, request: AccountQuery) -> Result<Vec<AccountProjection>, AccountError> {
        if request.account_id.as_str().trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        let segments = request
            .segments
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();
        let mut views = self.runtime.query(request.account_id.as_str(), &segments);
        if let (Some(max_age), Some(now)) = (request.max_age_seconds, request.now_unix_nanos) {
            for view in &mut views {
                view.stale =
                    now.get().saturating_sub(view.observed_at_unix_nanos.get()) > max_age.get();
            }
        }
        Ok(views)
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
            }
            Err(error) => {
                warn!(event = "account_refresh_failed", component = "account", account_id = %request.account_id, duration_ms = started.elapsed().as_millis(), error = %error, "account refresh failed");
                Err(error)
            }
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

    pub fn snapshot(&self) -> AccountsSnapshot {
        self.runtime.snapshot()
    }

    pub(crate) fn snapshot_shared(&self) -> Arc<AccountsSnapshot> {
        self.runtime.snapshot_shared()
    }

    pub fn set_trade_enabled(&mut self, enabled: bool) {
        self.trade_enabled = enabled;
    }

    pub fn capabilities(&self, account_id: Option<&str>) -> Vec<AccountCapability> {
        self.runtime
            .snapshot_shared()
            .accounts
            .iter()
            .filter(|view| account_id.is_none_or(|id| view.account_id == id))
            .map(|view| {
                // Account reports conservative account facts. Provider action
                // capabilities (transfers, earn, and order-type support) are
                // owned by Integration/Execution and must not be inferred
                // from a broker or segment string here.
                let can_trade = self.trade_enabled
                    && matches!(view.status, crate::domain::AccountStatus::Ready);
                let can_transfer = false;
                let can_hold_position = view
                    .observed_account_model
                    .or_else(|| {
                        view.configured_account_model
                            .as_deref()
                            .and_then(crate::domain::AccountModel::parse)
                    })
                    .is_some_and(|model| !matches!(model, crate::domain::AccountModel::NoMargin));
                let can_borrow = view
                    .configured_account_model
                    .as_deref()
                    .is_some_and(|model| {
                        matches!(
                            model.to_ascii_lowercase().as_str(),
                            "margin"
                                | "cross_margin"
                                | "isolated_margin"
                                | "contract"
                                | "contract_unified"
                                | "unified"
                                | "portfolio_margin"
                        )
                    });
                AccountCapability {
                    account_id: view.account_id.clone(),
                    segment_key: view.segment_key.clone(),
                    can_trade,
                    can_hold_assets: true,
                    can_hold_position,
                    can_borrow,
                    can_transfer_in: can_transfer,
                    can_transfer_out: can_transfer,
                    supported_order_types: if can_trade {
                        vec!["market".into(), "limit".into()]
                    } else {
                        Vec::new()
                    },
                    settlement_assets: Vec::new(),
                }
            })
            .collect()
    }

    pub fn fee_schedules(&self, account_id: Option<&str>) -> Vec<AccountFeeSchedule> {
        let mut schedules = Vec::new();
        for view in &self.runtime.snapshot_shared().accounts {
            if account_id.is_some_and(|id| view.account_id != id) {
                continue;
            }
            let profiles: Vec<_> = self
                .market_profiles
                .values()
                .filter(|profile| profile.segment_key == view.segment_key)
                .collect();
            if profiles.is_empty() {
                schedules.push(AccountFeeSchedule {
                    account_id: view.account_id.clone(),
                    segment_key: view.segment_key.clone(),
                    maker: None,
                    taker: None,
                    currency: None,
                    tier: None,
                    source: "unavailable".into(),
                });
            } else {
                schedules.extend(profiles.into_iter().map(|profile| AccountFeeSchedule {
                    account_id: view.account_id.clone(),
                    segment_key: view.segment_key.clone(),
                    maker: profile.maker_fee,
                    taker: profile.taker_fee,
                    currency: profile.fee_currency.clone(),
                    tier: profile.fee_tier.clone(),
                    source: "market_profile".into(),
                }));
            }
        }
        schedules
    }

    pub fn snapshot_query(&self, request: &AccountDataQuery) -> AccountsSnapshot {
        let symbol = request
            .symbol
            .as_ref()
            .map(|value| value.as_str().to_ascii_lowercase());
        let source = self.snapshot_shared();
        let accounts = source
            .accounts
            .iter()
            .filter(|view| {
                if request
                    .account_id
                    .as_deref()
                    .is_some_and(|id| view.account_id != id)
                {
                    return false;
                }
                if !request.segments.is_empty()
                    && !request
                        .segments
                        .iter()
                        .any(|value| value == &view.segment_key)
                {
                    return false;
                }
                symbol.as_deref().is_none_or(|needle| {
                    view.positions.iter().any(|value| {
                        value.instrument_id.to_ascii_lowercase().contains(needle)
                            || value
                                .market_id
                                .as_deref()
                                .is_some_and(|market| market.to_ascii_lowercase().contains(needle))
                    }) || view
                        .open_orders
                        .iter()
                        .any(|value| value.instrument_id.to_ascii_lowercase().contains(needle))
                })
            })
            .cloned()
            .collect();
        AccountsSnapshot {
            actor_id: source.actor_id.clone(),
            generation: source.generation,
            accounts,
        }
    }

    pub fn balances(
        &self,
        account_id: Option<&str>,
    ) -> Vec<(String, String, Vec<crate::domain::Balance>)> {
        self.runtime
            .snapshot_shared()
            .accounts
            .iter()
            .filter(|view| account_id.is_none_or(|id| view.account_id == id))
            .map(|view| {
                (
                    view.account_id.to_string(),
                    view.segment_key.to_string(),
                    view.balances.clone(),
                )
            })
            .collect()
    }

    pub fn balances_query(
        &self,
        request: &AccountDataQuery,
    ) -> Vec<(String, String, Vec<crate::domain::Balance>)> {
        let mut rows = self
            .balances(request.account_id.as_deref())
            .into_iter()
            .filter(|(_, segment, _)| {
                request.segments.is_empty() || request.segments.iter().any(|v| v == segment)
            })
            .map(|(account_id, segment, balances)| {
                let balances = balances
                    .into_iter()
                    .filter(|value| request.include_zero || !value.total.is_zero())
                    .collect();
                (account_id, segment, balances)
            })
            .collect::<Vec<_>>();
        paginate(&mut rows, request.page, request.page_size);
        rows
    }

    pub fn balances_query_with_rows(
        &self,
        request: &AccountDataQuery,
    ) -> (BalanceRows, Vec<AccountBalanceRow>) {
        let mut accounts = Vec::new();
        let mut rows = Vec::new();
        for view in &self.runtime.snapshot_shared().accounts {
            if request
                .account_id
                .as_deref()
                .is_some_and(|id| view.account_id != id)
                || (!request.segments.is_empty()
                    && !request
                        .segments
                        .iter()
                        .any(|segment| segment == &view.segment_key))
            {
                continue;
            }
            let balances: Vec<_> = view
                .balances
                .iter()
                .filter(|value| request.include_zero || !value.total.is_zero())
                .cloned()
                .collect();
            rows.extend(balances.iter().cloned().map(|balance| AccountBalanceRow {
                account_id: view.account_id.clone(),
                segment_key: view.segment_key.clone(),
                balance,
            }));
            accounts.push((
                view.account_id.to_string(),
                view.segment_key.to_string(),
                balances,
            ));
        }
        paginate(&mut accounts, request.page, request.page_size);
        paginate(&mut rows, request.page, request.page_size);
        (accounts, rows)
    }

    pub fn balance_rows_query(&self, request: &AccountDataQuery) -> Vec<AccountBalanceRow> {
        let mut rows = self
            .balances(request.account_id.as_deref())
            .into_iter()
            .filter(|(_, segment, _)| {
                request.segments.is_empty() || request.segments.iter().any(|v| v == segment)
            })
            .flat_map(|(account_id, segment_key, balances)| {
                balances.into_iter().filter_map(move |balance| {
                    if request.include_zero || !balance.total.is_zero() {
                        Some(AccountBalanceRow {
                            account_id: kairos_domain_types::AccountId::new(account_id.clone())
                                .expect("runtime projection account IDs are validated"),
                            segment_key: crate::domain::SegmentKey::new(segment_key.clone())
                                .expect("runtime projection segment keys are validated"),
                            balance,
                        })
                    } else {
                        None
                    }
                })
            })
            .collect::<Vec<_>>();
        paginate(&mut rows, request.page, request.page_size);
        rows
    }

    pub fn positions(
        &self,
        account_id: Option<&str>,
    ) -> Vec<(String, String, Vec<crate::domain::Position>)> {
        self.runtime
            .snapshot_shared()
            .accounts
            .iter()
            .filter(|view| account_id.is_none_or(|id| view.account_id == id))
            .map(|view| {
                (
                    view.account_id.to_string(),
                    view.segment_key.to_string(),
                    view.positions.clone(),
                )
            })
            .collect()
    }

    pub fn positions_query(
        &self,
        request: &AccountDataQuery,
    ) -> Vec<(String, String, Vec<crate::domain::Position>)> {
        let symbol = request.symbol.as_deref().map(str::to_ascii_lowercase);
        let mut rows = self
            .positions(request.account_id.as_deref())
            .into_iter()
            .filter(|(_, segment, _)| {
                request.segments.is_empty() || request.segments.iter().any(|v| v == segment)
            })
            .map(|(account_id, segment, positions)| {
                let positions = positions
                    .into_iter()
                    .filter(|value| {
                        symbol.as_deref().is_none_or(|needle| {
                            value.instrument_id.to_ascii_lowercase().contains(needle)
                                || value.market_id.as_deref().is_some_and(|market| {
                                    market.to_ascii_lowercase().contains(needle)
                                })
                        })
                    })
                    .collect();
                (account_id, segment, positions)
            })
            .collect::<Vec<_>>();
        paginate(&mut rows, request.page, request.page_size);
        rows
    }

    pub fn open_orders(
        &self,
        account_id: Option<&str>,
    ) -> Vec<(String, String, Vec<crate::domain::OpenOrder>)> {
        self.runtime
            .snapshot_shared()
            .accounts
            .iter()
            .filter(|view| account_id.is_none_or(|id| view.account_id == id))
            .map(|view| {
                (
                    view.account_id.to_string(),
                    view.segment_key.to_string(),
                    view.open_orders.clone(),
                )
            })
            .collect()
    }

    pub fn open_orders_query(
        &self,
        request: &AccountDataQuery,
    ) -> Vec<(String, String, Vec<crate::domain::OpenOrder>)> {
        let symbol = request.symbol.as_deref().map(str::to_ascii_lowercase);
        let mut rows = self
            .open_orders(request.account_id.as_deref())
            .into_iter()
            .filter(|(_, segment, _)| {
                request.segments.is_empty() || request.segments.iter().any(|v| v == segment)
            })
            .map(|(account_id, segment, orders)| {
                let mut orders = orders
                    .into_iter()
                    .filter(|value| {
                        symbol.as_deref().is_none_or(|needle| {
                            value.instrument_id.to_ascii_lowercase().contains(needle)
                        })
                    })
                    .collect::<Vec<_>>();
                if let Some(limit) = request.limit {
                    orders.truncate(limit);
                }
                (account_id, segment, orders)
            })
            .collect::<Vec<_>>();
        paginate(&mut rows, request.page, request.page_size);
        rows
    }

    pub fn set_market_profile(&mut self, profile: AccountMarketProfile) {
        self.market_profiles.insert(
            (profile.segment_key.clone(), profile.market_id.to_string()),
            profile,
        );
    }

    pub(crate) fn attach_market_profile_source(&mut self, source: AccountMarketProfileGateway) {
        self.profile_source = Some(source);
    }

    pub fn refresh_market_profile(
        &mut self,
        request: AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, AccountError> {
        let profile = self
            .profile_source
            .as_mut()
            .ok_or_else(|| AccountError::Source("market profile source is not configured".into()))?
            .fetch(&request)
            .map_err(AccountError::Source)?;
        self.set_market_profile(profile.clone());
        Ok(profile)
    }

    pub async fn refresh_market_profile_async(
        &mut self,
        request: AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, AccountError> {
        let Some(source) = self.async_profile_source.as_mut() else {
            return Err(AccountError::Source(
                "async market profile source is not configured".into(),
            ));
        };
        let profile = source.fetch(&request).await.map_err(AccountError::Source)?;
        self.set_market_profile(profile.clone());
        Ok(profile)
    }

    pub fn market_profile(
        &self,
        request: &AccountMarketProfileRequest,
    ) -> Option<AccountMarketProfile> {
        self.market_profiles
            .get(&(request.segment_key.clone(), request.market_id.to_string()))
            .cloned()
    }

    pub fn market_profiles(&self) -> Vec<AccountMarketProfile> {
        self.market_profiles.values().cloned().collect()
    }

    pub fn apply_simulated_fill(
        &mut self,
        fill: crate::domain::AccountFill,
    ) -> Result<(), AccountError> {
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
            }
            Err(error) => {
                warn!(event = "account_fill_rejected", component = "account", error = %error, "account fill rejected");
                Err(error)
            }
        }
    }

    pub fn mark_to_market(&mut self, request: MarkToMarket) -> Result<(), AccountError> {
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

    pub(crate) fn attach_async_sources(
        &mut self,
        snapshot: AccountAsyncSnapshotGateway,
        profile: Option<AccountAsyncMarketProfileGateway>,
    ) {
        self.async_snapshot_source = Some(snapshot);
        self.async_profile_source = profile;
    }

    pub async fn refresh_report_async(
        &mut self,
        request: RefreshAccount,
    ) -> Result<AccountRefreshReport, AccountError> {
        if self.async_snapshot_source.is_none() {
            return Err(AccountError::Source(
                "async account snapshot source is not configured".into(),
            ));
        }
        let account_id = request.account_id.to_string();
        let segments = self.selected_refresh_segments(&request)?;
        let fetches = self
            .async_snapshot_source
            .as_mut()
            .expect("async snapshot source checked")
            .fetch(segments)
            .await;
        self.apply_refresh_fetches(&account_id, fetches)
    }

    pub(crate) fn take_async_snapshot_source(&mut self) -> Option<AccountAsyncSnapshotGateway> {
        self.async_snapshot_source.take()
    }

    #[cfg(test)]
    pub(crate) fn async_source_counts(&self) -> (usize, usize) {
        (
            self.async_snapshot_source
                .as_ref()
                .map_or(0, |source| source.len()),
            self.async_profile_source
                .as_ref()
                .map_or(0, |source| source.len()),
        )
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

fn paginate<T>(rows: &mut Vec<T>, page: Option<usize>, page_size: Option<usize>) {
    let Some(page_size) = page_size.filter(|value| *value > 0) else {
        return;
    };
    let page = page.unwrap_or(1).max(1);
    let start = page.saturating_sub(1).saturating_mul(page_size);
    if start >= rows.len() {
        rows.clear();
        return;
    }
    let end = start.saturating_add(page_size).min(rows.len());
    *rows = rows.drain(start..end).collect();
}
