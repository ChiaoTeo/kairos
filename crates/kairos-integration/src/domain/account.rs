//! Provider-neutral facts produced by private account connections.
//!
//! These are deliberately external facts, not account-domain entities.  The
//! account module owns the mapping from these values into its state model.

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ExternalAccountIdentity {
    pub broker: String,
    pub account_id: String,
}

impl ExternalAccountIdentity {
    pub fn new(broker: impl Into<String>, account_id: impl Into<String>) -> Result<Self, String> {
        let value = Self {
            broker: broker.into(),
            account_id: account_id.into(),
        };
        if value.broker.trim().is_empty() || value.account_id.trim().is_empty() {
            return Err("broker and account_id are required".into());
        }
        Ok(value)
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ExternalAccountSegment {
    pub identity: ExternalAccountIdentity,
    pub segment_key: String,
    pub environment: String,
    pub account_model: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExternalAccountModel {
    NoMargin,
    Margin,
    Contract,
    ContractUnified,
    Unified,
    PortfolioMargin,
}

impl ExternalAccountModel {
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "no_margin" | "spot" => Some(Self::NoMargin),
            "margin" | "cross_margin" | "isolated_margin" => Some(Self::Margin),
            "contract" | "futures" | "swap" => Some(Self::Contract),
            "contract_unified" => Some(Self::ContractUnified),
            "unified" | "multi_currency_margin" => Some(Self::Unified),
            "portfolio_margin" => Some(Self::PortfolioMargin),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExternalMarginMode {
    Cross,
    Isolated,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExternalPositionMode {
    OneWay,
    Hedge,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalDecimal {
    pub mantissa: i64,
    pub scale: u8,
}

impl ExternalDecimal {
    pub const fn new(mantissa: i64, scale: u8) -> Self {
        Self { mantissa, scale }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalBalance {
    pub asset_id: String,
    pub asset_code: String,
    pub total: ExternalDecimal,
    pub available: Option<ExternalDecimal>,
    pub locked: Option<ExternalDecimal>,
    pub borrowed: Option<ExternalDecimal>,
    pub interest: Option<ExternalDecimal>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalPosition {
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub quantity: ExternalDecimal,
    pub average_price: Option<ExternalDecimal>,
    pub mark_price: Option<ExternalDecimal>,
    pub unrealized_pnl: Option<ExternalDecimal>,
    pub realized_pnl: Option<ExternalDecimal>,
    pub updated_at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExternalAccountStatus {
    #[default]
    Unknown,
    Ready,
    Reconciling,
    TypeMismatch,
    Suspended,
    Unavailable,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalAccountSnapshot {
    pub segment_key: String,
    pub balances: Vec<ExternalBalance>,
    #[serde(default)]
    pub collateral: Vec<ExternalBalance>,
    pub positions: Vec<ExternalPosition>,
    #[serde(default)]
    pub open_orders: Vec<ExternalOpenOrder>,
    pub status: ExternalAccountStatus,
    pub observed_at_unix_nanos: u64,
    pub equity: Option<ExternalDecimal>,
    pub initial_equity: Option<ExternalDecimal>,
    pub net_profit: Option<ExternalDecimal>,
    #[serde(default)]
    pub account_model: Option<ExternalAccountModel>,
    #[serde(default)]
    pub margin_mode: Option<ExternalMarginMode>,
    #[serde(default)]
    pub position_mode: Option<ExternalPositionMode>,
    #[serde(default)]
    pub partial: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalOpenOrder {
    pub order_id: String,
    pub venue_order_id: Option<String>,
    pub instrument_id: String,
    pub side: String,
    pub quantity: ExternalDecimal,
    pub filled_quantity: ExternalDecimal,
    pub status: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExternalOrderStatus {
    Acknowledged,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
    Expired,
    Unknown,
}

impl Default for ExternalOrderStatus {
    fn default() -> Self {
        Self::Unknown
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalOrderEvent {
    pub order_id: String,
    pub status: ExternalOrderStatus,
    pub venue_order_id: Option<String>,
    pub filled_quantity: Option<ExternalDecimal>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalFillEvent {
    pub fill_id: String,
    pub order_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub side: String,
    pub quantity: ExternalDecimal,
    pub price: ExternalDecimal,
    #[serde(default)]
    pub fee_asset: Option<String>,
    #[serde(default)]
    pub fee_amount: Option<ExternalDecimal>,
    pub occurred_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExternalAccountEvent {
    Snapshot(ExternalAccountSnapshot),
    Order(ExternalOrderEvent),
    Fill(ExternalFillEvent),
    Batch(Vec<ExternalAccountEvent>),
}
