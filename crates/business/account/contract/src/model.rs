//! Public Account snapshot models.

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Decimal {
    pub mantissa: i64,
    pub scale: u8,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AccountModel {
    NoMargin,
    Margin,
    Contract,
    ContractUnified,
    Unified,
    PortfolioMargin,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum AccountStatus {
    #[default]
    Unknown,
    Ready,
    Reconciling,
    TypeMismatch,
    Suspended,
    Unavailable,
}

impl AccountStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::Ready => "ready",
            Self::Reconciling => "reconciling",
            Self::TypeMismatch => "type_mismatch",
            Self::Suspended => "suspended",
            Self::Unavailable => "unavailable",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum MarginMode {
    Cross,
    Isolated,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum PositionMode {
    OneWay,
    Hedge,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Balance {
    pub asset_id: String,
    pub asset_code: String,
    pub total: Decimal,
    pub available: Option<Decimal>,
    pub locked: Option<Decimal>,
    pub borrowed: Option<Decimal>,
    pub interest: Option<Decimal>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Position {
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub quantity: Decimal,
    pub average_price: Option<Decimal>,
    pub mark_price: Option<Decimal>,
    pub unrealized_pnl: Option<Decimal>,
    pub realized_pnl: Option<Decimal>,
    pub updated_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct OpenOrder {
    pub order_id: String,
    pub venue_order_id: Option<String>,
    pub instrument_id: String,
    pub side: String,
    pub quantity: Decimal,
    pub filled_quantity: Decimal,
    pub status: String,
}

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

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AccountsSnapshot {
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub accounts: Vec<AccountProjection>,
}
