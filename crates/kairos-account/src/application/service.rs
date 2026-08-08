use super::market_profile::{AccountMarketProfile, AccountMarketProfileRequest};
use super::protocol::{AccountMarketProfileSource, OrderRiskRequest};
use crate::application::protocol::{AccountSnapshotSource, AccountStateStore};
use crate::domain::{AccountSegment, AccountView, OrderEvent, OrderState};
use crate::services::actor::AccountActor;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountQuery {
    pub account_id: String,
    pub segments: Vec<String>,
    pub max_age_seconds: Option<u64>,
    pub now_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RefreshAccount {
    pub account_id: String,
    pub segments: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReconcileAccount {
    pub account_id: String,
    pub segments: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountRefreshIssue {
    pub segment_key: String,
    pub error: String,
    pub elapsed_ms: u64,
    pub diagnostic_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountDifference {
    pub field: String,
    pub key: String,
    pub local: crate::domain::DecimalValue,
    pub external: crate::domain::DecimalValue,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountRefreshReport {
    pub account_id: String,
    pub refreshed_segments: Vec<String>,
    pub issues: Vec<AccountRefreshIssue>,
    #[serde(default)]
    pub differences: Vec<AccountDifference>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountsSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub accounts: Vec<AccountView>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OrderQuery {
    pub account_id: Option<String>,
    pub order_id: Option<String>,
}

/// Filters for account-facing live query views.  This is intentionally a
/// business query type so CLI and server expose the same semantics.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct AccountDataQuery {
    pub account_id: Option<String>,
    pub segments: Vec<String>,
    pub symbol: Option<String>,
    pub include_zero: bool,
    pub limit: Option<usize>,
    pub page: Option<usize>,
    pub page_size: Option<usize>,
}

/// Account-owned capability projection used by CLI, server, and future
/// execution planning.  It intentionally contains no connector details.
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountCapability {
    pub account_id: String,
    pub segment_key: String,
    pub can_trade: bool,
    pub can_hold_assets: bool,
    pub can_hold_position: bool,
    pub can_borrow: bool,
    pub can_transfer_in: bool,
    pub can_transfer_out: bool,
    pub supported_order_types: Vec<String>,
    pub settlement_assets: Vec<String>,
}

/// Account fee projection.  A live connector may not expose an account fee
/// until a market profile is refreshed, so rates are optional rather than
/// inventing a default.
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountFeeSchedule {
    pub account_id: String,
    pub segment_key: String,
    pub maker: Option<crate::domain::DecimalValue>,
    pub taker: Option<crate::domain::DecimalValue>,
    pub currency: Option<String>,
    pub tier: Option<String>,
    pub source: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountBalanceRow {
    pub account_id: String,
    pub segment_key: String,
    pub balance: crate::domain::Balance,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LoginAccount {
    pub account_id: String,
    pub segments: Vec<String>,
    pub connection_ids: Vec<String>,
    pub observed_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AccountSession {
    pub session_id: String,
    pub account_id: String,
    pub segment_keys: Vec<String>,
    pub connection_ids: Vec<String>,
    pub logged_in_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct LoginResult {
    pub session: AccountSession,
    pub snapshot: AccountsSnapshot,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum AccountError {
    #[error("invalid account request: {0}")]
    Invalid(String),
    #[error("account source failed: {0}")]
    Source(String),
    #[error("account persistence failed: {0}")]
    Persistence(String),
    #[error("account publication failed: {0}")]
    Publication(String),
}

pub struct AccountApplication {
    actor: AccountActor,
    market_profiles: std::collections::BTreeMap<(String, String), AccountMarketProfile>,
    profile_source: Option<Box<dyn AccountMarketProfileSource>>,
    trade_enabled: bool,
}

impl AccountApplication {
    pub fn new(actor: AccountActor) -> Self {
        Self {
            actor,
            market_profiles: std::collections::BTreeMap::new(),
            profile_source: None,
            trade_enabled: true,
        }
    }

    pub fn has_stream(&self) -> bool {
        self.actor.has_stream()
    }

    pub fn attach_stream(&mut self, stream: Box<dyn super::protocol::AccountStreamSource>) {
        self.actor.attach_stream(stream);
    }

    pub fn poll_stream_once(&mut self) -> Result<bool, AccountError> {
        self.actor.poll_stream_once().map_err(AccountError::Source)
    }

    pub fn query(&self, request: AccountQuery) -> Result<Vec<AccountView>, AccountError> {
        if request.account_id.trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        let mut views = self.actor.query(&request.account_id, &request.segments);
        if let (Some(max_age), Some(now)) = (request.max_age_seconds, request.now_unix_nanos) {
            let max_age_nanos = max_age.saturating_mul(1_000_000_000);
            for view in &mut views {
                view.account.state.stale =
                    now.saturating_sub(view.account.state.observed_at_unix_nanos) > max_age_nanos;
            }
        }
        Ok(views)
    }

    pub fn refresh(&mut self, request: RefreshAccount) -> Result<usize, AccountError> {
        if request.account_id.trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        self.actor
            .refresh(&request.account_id, &request.segments)
            .map_err(AccountError::Source)
    }

    pub fn refresh_report(
        &mut self,
        request: RefreshAccount,
    ) -> Result<AccountRefreshReport, AccountError> {
        if request.account_id.trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        self.actor
            .refresh_report(&request.account_id, &request.segments)
            .map_err(AccountError::Source)
    }

    pub fn reconcile(&mut self, request: ReconcileAccount) -> Result<usize, AccountError> {
        if request.account_id.trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        self.actor
            .reconcile(&request.account_id, &request.segments)
            .map_err(AccountError::Source)
    }

    pub fn reconcile_report(
        &mut self,
        request: ReconcileAccount,
    ) -> Result<AccountRefreshReport, AccountError> {
        if request.account_id.trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        self.actor
            .reconcile_report(&request.account_id, &request.segments)
            .map_err(AccountError::Source)
    }

    pub fn snapshot(&self) -> AccountsSnapshot {
        self.actor.snapshot()
    }

    pub fn set_trade_enabled(&mut self, enabled: bool) {
        self.trade_enabled = enabled;
    }

    pub fn login(&mut self, request: LoginAccount) -> Result<LoginResult, AccountError> {
        if request.account_id.trim().is_empty() {
            return Err(AccountError::Invalid("account_id is required".into()));
        }
        self.refresh(RefreshAccount {
            account_id: request.account_id.clone(),
            segments: request.segments.clone(),
        })?;
        let configured_segments = self
            .actor
            .snapshot()
            .accounts
            .into_iter()
            .filter(|view| view.account.segment.identity.account_id == request.account_id)
            .map(|view| view.account.segment.segment_key)
            .collect::<Vec<_>>();
        if configured_segments.is_empty() {
            return Err(AccountError::Invalid(format!(
                "account is not configured: {}",
                request.account_id
            )));
        }
        let segment_keys = if request.segments.is_empty() {
            configured_segments
        } else {
            request.segments
        };
        let session = AccountSession {
            session_id: format!(
                "account.{}.{}",
                request.account_id, request.observed_at_unix_nanos
            ),
            account_id: request.account_id,
            segment_keys,
            connection_ids: request.connection_ids,
            logged_in_at_unix_nanos: request.observed_at_unix_nanos,
        };
        Ok(LoginResult {
            session,
            snapshot: self.snapshot(),
        })
    }

    pub fn logout(&self, session: &AccountSession) -> Result<(), AccountError> {
        if session.session_id.trim().is_empty() {
            return Err(AccountError::Invalid("session_id is required".into()));
        }
        Ok(())
    }

    pub fn capabilities(&self, account_id: Option<&str>) -> Vec<AccountCapability> {
        self.actor
            .snapshot()
            .accounts
            .into_iter()
            .filter(|view| {
                account_id.is_none_or(|id| view.account.segment.identity.account_id == id)
            })
            .map(|view| {
                let segment = &view.account.segment;
                let key = segment.segment_key.to_ascii_lowercase();
                let broker = segment.identity.broker.to_ascii_lowercase();
                let can_trade = self.trade_enabled && !matches!(key.as_str(), "funding" | "earn");
                // Transfer is an integration capability, not a property of
                // every account snapshot.  The current native adapter exists
                // only for live Binance accounts; paper/other venues must
                // report unavailable instead of advertising a false action.
                let can_transfer = broker == "binance"
                    && !matches!(
                        segment.environment.to_ascii_lowercase().as_str(),
                        "paper" | "simulated"
                    );
                let can_hold_position = !matches!(key.as_str(), "spot" | "funding" | "earn");
                let can_borrow = segment.account_model.as_deref().is_some_and(|model| {
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
                    account_id: segment.identity.account_id.clone(),
                    segment_key: segment.segment_key.clone(),
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
        for view in self.actor.snapshot().accounts {
            let segment = &view.account.segment;
            if account_id.is_some_and(|id| segment.identity.account_id != id) {
                continue;
            }
            let profiles: Vec<_> = self
                .market_profiles
                .values()
                .filter(|profile| profile.segment_key == segment.segment_key)
                .collect();
            if profiles.is_empty() {
                schedules.push(AccountFeeSchedule {
                    account_id: segment.identity.account_id.clone(),
                    segment_key: segment.segment_key.clone(),
                    maker: None,
                    taker: None,
                    currency: None,
                    tier: None,
                    source: "unavailable".into(),
                });
            } else {
                schedules.extend(profiles.into_iter().map(|profile| AccountFeeSchedule {
                    account_id: segment.identity.account_id.clone(),
                    segment_key: segment.segment_key.clone(),
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
        let symbol = request.symbol.as_deref().map(str::to_ascii_lowercase);
        let mut snapshot = self.snapshot();
        snapshot.accounts.retain(|view| {
            let account = &view.account;
            if request
                .account_id
                .as_deref()
                .is_some_and(|id| account.segment.identity.account_id != id)
            {
                return false;
            }
            if !request.segments.is_empty()
                && !request
                    .segments
                    .iter()
                    .any(|value| value == &account.segment.segment_key)
            {
                return false;
            }
            symbol.as_deref().is_none_or(|needle| {
                account.state.positions.values().any(|value| {
                    value.instrument_id.to_ascii_lowercase().contains(needle)
                        || value
                            .market_id
                            .as_deref()
                            .is_some_and(|market| market.to_ascii_lowercase().contains(needle))
                }) || account
                    .state
                    .open_orders
                    .values()
                    .any(|value| value.instrument_id.to_ascii_lowercase().contains(needle))
            })
        });
        snapshot
    }

    pub fn orders(&self, query: OrderQuery) -> Vec<OrderState> {
        self.actor
            .snapshot()
            .accounts
            .into_iter()
            .filter(|view| {
                query
                    .account_id
                    .as_deref()
                    .is_none_or(|account_id| view.account.segment.identity.account_id == account_id)
            })
            .flat_map(|view| view.account.state.orders.into_values())
            .filter(|order| {
                query
                    .order_id
                    .as_deref()
                    .is_none_or(|id| order.request.order_id == id)
            })
            .collect()
    }

    pub fn balances(
        &self,
        account_id: Option<&str>,
    ) -> Vec<(String, String, Vec<crate::domain::Balance>)> {
        self.actor
            .snapshot()
            .accounts
            .into_iter()
            .filter(|view| {
                account_id.is_none_or(|id| view.account.segment.identity.account_id == id)
            })
            .map(|view| {
                (
                    view.account.segment.identity.account_id,
                    view.account.segment.segment_key,
                    view.account.state.balances.into_values().collect(),
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
                    .filter(|value| request.include_zero || value.total.mantissa != 0)
                    .collect();
                (account_id, segment, balances)
            })
            .collect::<Vec<_>>();
        paginate(&mut rows, request.page, request.page_size);
        rows
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
                    if request.include_zero || balance.total.mantissa != 0 {
                        Some(AccountBalanceRow {
                            account_id: account_id.clone(),
                            segment_key: segment_key.clone(),
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
        self.actor
            .snapshot()
            .accounts
            .into_iter()
            .filter(|view| {
                account_id.is_none_or(|id| view.account.segment.identity.account_id == id)
            })
            .map(|view| {
                (
                    view.account.segment.identity.account_id,
                    view.account.segment.segment_key,
                    view.account.state.positions.into_values().collect(),
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
        self.actor
            .snapshot()
            .accounts
            .into_iter()
            .filter(|view| {
                account_id.is_none_or(|id| view.account.segment.identity.account_id == id)
            })
            .map(|view| {
                (
                    view.account.segment.identity.account_id,
                    view.account.segment.segment_key,
                    view.account.state.open_orders.into_values().collect(),
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
            (profile.segment_key.clone(), profile.market_id.clone()),
            profile,
        );
    }

    pub fn attach_market_profile_source(&mut self, source: Box<dyn AccountMarketProfileSource>) {
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
            .fetch_profile(&request)
            .map_err(AccountError::Source)?;
        self.set_market_profile(profile.clone());
        Ok(profile)
    }

    pub fn market_profile(
        &self,
        request: &AccountMarketProfileRequest,
    ) -> Option<AccountMarketProfile> {
        self.market_profiles
            .get(&(request.segment_key.clone(), request.market_id.clone()))
            .cloned()
    }

    pub fn market_profiles(&self) -> Vec<AccountMarketProfile> {
        self.market_profiles.values().cloned().collect()
    }

    pub fn plan_order(
        &mut self,
        request: crate::domain::OrderRequest,
        at: u64,
    ) -> Result<OrderState, AccountError> {
        self.actor
            .plan_order(request.clone(), at)
            .map_err(AccountError::Invalid)?;
        self.actor
            .snapshot()
            .accounts
            .iter()
            .flat_map(|view| view.account.state.orders.values())
            .find(|order| order.request.order_id == request.order_id)
            .cloned()
            .ok_or_else(|| AccountError::Invalid("planned order is missing".into()))
    }

    pub fn plan_order_with_risk(
        &mut self,
        request: crate::domain::OrderRequest,
        at: u64,
        risk: &mut dyn crate::application::protocol::OrderRisk,
    ) -> Result<OrderState, AccountError> {
        let risk_request = OrderRiskRequest {
            reservation_id: request.order_id.clone(),
            account_id: request.account_id.clone(),
            segment_key: request.segment_key.clone(),
            instrument_id: request.instrument_id.clone(),
            quantity: request.quantity,
            price: request.limit_price,
        };
        risk.reserve(&risk_request).map_err(AccountError::Invalid)?;
        match self.plan_order(request, at) {
            Ok(order) => Ok(order),
            Err(error) => {
                let _ = risk.release(&risk_request.reservation_id);
                Err(error)
            }
        }
    }

    pub fn apply_order_event(&mut self, event: OrderEvent) -> Result<OrderState, AccountError> {
        let order_id = event.order_id.clone();
        self.actor
            .apply_order_event(event)
            .map_err(AccountError::Invalid)?;
        self.actor
            .snapshot()
            .accounts
            .iter()
            .flat_map(|view| view.account.state.orders.values())
            .find(|order| order.request.order_id == order_id)
            .cloned()
            .ok_or_else(|| AccountError::Invalid("submitted order is missing".into()))
    }

    pub fn apply_fill(&mut self, fill: crate::domain::AccountFill) -> Result<(), AccountError> {
        self.actor.apply_fill(fill).map_err(AccountError::Invalid)
    }

    pub fn apply_order_event_with_risk(
        &mut self,
        event: OrderEvent,
        risk: &mut dyn crate::application::protocol::OrderRisk,
    ) -> Result<OrderState, AccountError> {
        let order_id = event.order_id.clone();
        let status = event.status;
        let order = self.apply_order_event(event)?;
        match status {
            crate::domain::OrderStatus::Filled => {
                risk.consume(&order_id).map_err(AccountError::Invalid)?;
            }
            crate::domain::OrderStatus::Canceled
            | crate::domain::OrderStatus::Rejected
            | crate::domain::OrderStatus::Expired => {
                risk.release(&order_id).map_err(AccountError::Invalid)?;
            }
            _ => {}
        }
        Ok(order)
    }

    pub fn with_dependencies(
        segments: Vec<AccountSegment>,
        source: Box<dyn AccountSnapshotSource>,
        store: Option<Box<dyn AccountStateStore>>,
    ) -> Result<Self, AccountError> {
        AccountActor::new(segments, source, store)
            .map(Self::new)
            .map_err(AccountError::Invalid)
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
