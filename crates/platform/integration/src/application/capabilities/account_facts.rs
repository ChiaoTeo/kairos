//! Provider-neutral facts produced by private account connections.
//!
//! These are deliberately external facts, not account-domain entities.  The
//! account module owns the mapping from these values into its state model.

use serde::{Deserialize, Serialize};

use crate::domain::{ParticipantKind, ParticipantRef, ProviderInstrumentRef};
use kairos_primitives::{
    AccountId, AssetId, Currency, OrderId, RemoteOrderId, SegmentKey, UnixNanos,
};

/// Preserve the provider-owned identity exactly as observed. Canonical
/// instrument and market identity is resolved by Reference in business
/// composition, never synthesized by Integration.
pub fn external_instrument_ref(
    kind: ParticipantKind,
    participant: &str,
    instrument_type: &str,
    source_symbol: &str,
) -> Result<ProviderInstrumentRef, String> {
    ProviderInstrumentRef::new(
        ParticipantRef::new(kind, participant)?,
        Some(crate::domain::ParticipantInstrumentTypeRef::new(
            instrument_type,
        )?),
        source_symbol,
    )
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ExternalAccountIdentity {
    pub broker: String,
    pub account_id: AccountId,
}

impl ExternalAccountIdentity {
    pub fn new(broker: impl Into<String>, account_id: impl Into<String>) -> Result<Self, String> {
        let broker = broker.into();
        if broker.trim().is_empty() {
            return Err("broker and account_id are required".into());
        }
        Ok(Self {
            broker,
            account_id: AccountId::new(account_id).map_err(|error| error.to_string())?,
        })
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ExternalAccountSegment {
    pub identity: ExternalAccountIdentity,
    pub segment_key: SegmentKey,
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalBalance {
    pub asset_id: AssetId,
    pub asset_code: Currency,
    pub total: ExternalDecimal,
    pub available: Option<ExternalDecimal>,
    pub locked: Option<ExternalDecimal>,
    pub borrowed: Option<ExternalDecimal>,
    pub interest: Option<ExternalDecimal>,
}

impl Default for ExternalBalance {
    fn default() -> Self {
        Self {
            asset_id: AssetId::new("asset:unknown").expect("canonical default asset id"),
            asset_code: Currency::new("UNKNOWN").expect("canonical default currency"),
            total: ExternalDecimal::default(),
            available: None,
            locked: None,
            borrowed: None,
            interest: None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalPosition {
    pub provider_instrument: ProviderInstrumentRef,
    pub quantity: ExternalDecimal,
    pub average_price: Option<ExternalDecimal>,
    pub mark_price: Option<ExternalDecimal>,
    pub unrealized_pnl: Option<ExternalDecimal>,
    pub realized_pnl: Option<ExternalDecimal>,
    pub updated_at_unix_nanos: UnixNanos,
}

impl Default for ExternalPosition {
    fn default() -> Self {
        Self {
            provider_instrument: ProviderInstrumentRef::new(
                ParticipantRef::new(ParticipantKind::DataProvider, "unknown")
                    .expect("static participant identity"),
                None,
                "UNKNOWN",
            )
            .expect("static provider instrument"),
            quantity: ExternalDecimal::default(),
            average_price: None,
            mark_price: None,
            unrealized_pnl: None,
            realized_pnl: None,
            updated_at_unix_nanos: UnixNanos::new(0),
        }
    }
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
    pub segment_key: SegmentKey,
    pub balances: Vec<ExternalBalance>,
    #[serde(default)]
    pub collateral: Vec<ExternalBalance>,
    pub positions: Vec<ExternalPosition>,
    #[serde(default)]
    pub open_orders: Vec<ExternalOpenOrder>,
    pub status: ExternalAccountStatus,
    pub observed_at_unix_nanos: UnixNanos,
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalOpenOrder {
    pub order_id: OrderId,
    pub remote_order_id: Option<RemoteOrderId>,
    pub provider_instrument: ProviderInstrumentRef,
    pub side: kairos_primitives::OrderSide,
    pub quantity: ExternalDecimal,
    pub filled_quantity: ExternalDecimal,
    pub status: kairos_primitives::OrderStatus,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExternalOrderStatus {
    Acknowledged,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
    Expired,
    #[default]
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalOrderEvent {
    pub order_id: OrderId,
    pub status: ExternalOrderStatus,
    pub remote_order_id: Option<RemoteOrderId>,
    pub filled_quantity: Option<ExternalDecimal>,
    pub occurred_at_unix_nanos: UnixNanos,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalFillEvent {
    pub fill_id: kairos_primitives::FillId,
    pub order_id: OrderId,
    pub segment_key: SegmentKey,
    pub provider_instrument: ProviderInstrumentRef,
    pub side: String,
    pub quantity: ExternalDecimal,
    pub price: ExternalDecimal,
    #[serde(default)]
    pub fee_asset: Option<Currency>,
    #[serde(default)]
    pub fee_amount: Option<ExternalDecimal>,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExternalAccountEvent {
    Snapshot(ExternalAccountSnapshot),
    Order(ExternalOrderEvent),
    Fill(ExternalFillEvent),
    Batch(Vec<ExternalAccountEvent>),
}

pub type ExternalAccountEventEnvelope =
    crate::application::ExternalEventEnvelope<ExternalAccountEvent>;
