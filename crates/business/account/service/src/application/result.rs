use crate::domain::{
    Account, AccountModel, AccountStatus, Balance, Decimal, MarginMode, OpenOrder, Position,
    PositionMode,
};

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AccountProjection {
    pub account_id: String,
    pub segment_key: String,
    pub environment: String,
    pub broker: String,
    pub configured_account_model: Option<String>,
    pub observed_account_model: Option<AccountModel>,
    pub status: AccountStatus,
    pub stale: bool,
    pub observed_at_unix_nanos: u64,
    pub generation: u64,
    pub event_sequence: u64,
    pub equity: Option<Decimal>,
    pub initial_equity: Option<Decimal>,
    pub net_profit: Option<Decimal>,
    pub margin_mode: Option<MarginMode>,
    pub position_mode: Option<PositionMode>,
    pub balances: Vec<Balance>,
    pub collateral: Vec<Balance>,
    pub positions: Vec<Position>,
    pub open_orders: Vec<OpenOrder>,
}

impl AccountProjection {
    pub(crate) fn from_account(account: &Account) -> Self {
        let segment = account.segment();
        let state = account.state();
        Self {
            account_id: segment.identity.account_id.to_string(),
            segment_key: segment.segment_key.to_string(),
            environment: segment.environment.clone(),
            broker: segment.identity.broker.clone(),
            configured_account_model: segment.account_model.clone(),
            observed_account_model: state.observed_account_model(),
            status: state.status(),
            stale: state.stale(),
            observed_at_unix_nanos: state.observed_at_unix_nanos(),
            generation: state.generation(),
            event_sequence: state.event_sequence(),
            equity: state.equity(),
            initial_equity: state.initial_equity(),
            net_profit: state.net_profit(),
            margin_mode: state.margin_mode(),
            position_mode: state.position_mode(),
            balances: state.balances().values().cloned().collect(),
            collateral: state.collateral().values().cloned().collect(),
            positions: state.positions().values().cloned().collect(),
            open_orders: state.open_orders().values().cloned().collect(),
        }
    }
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
    pub local: Decimal,
    pub external: Decimal,
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
    pub accounts: Vec<AccountProjection>,
}

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

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountFeeSchedule {
    pub account_id: String,
    pub segment_key: String,
    pub maker: Option<Decimal>,
    pub taker: Option<Decimal>,
    pub currency: Option<String>,
    pub tier: Option<String>,
    pub source: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
pub struct AccountBalanceRow {
    pub account_id: String,
    pub segment_key: String,
    pub balance: Balance,
}
