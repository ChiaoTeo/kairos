//! Participant-neutral facts produced by private account connections.
//!
//! These are deliberately external facts, not account-domain entities.  The
//! account module owns the mapping from these values into its state model.

use std::collections::BTreeMap;

use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::execution::OrderId;
use kairos_primitives::integration::RemoteOrderId;
use kairos_primitives::reference::{AssetId, Currency, MarketId, Symbol};
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

use crate::domain::{ParticipantInstrumentRef, ParticipantKind, ParticipantRef};

/// Preserve the provider-owned identity exactly as observed. Canonical
/// instrument and market identity is resolved by Reference in business
/// composition, never synthesized by Integration.
pub fn external_instrument_ref(
    kind: ParticipantKind,
    participant: &str,
    instrument_type: &str,
    source_symbol: &str,
) -> Result<ParticipantInstrumentRef, String> {
    ParticipantInstrumentRef::new(
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

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalAccountProfile {
    pub account_model: ExternalAccountModel,
    pub provider_account_model: Option<String>,
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

/// Compatibility name for the participant-neutral Integration decimal.
pub use super::decimal::DecimalValue as ExternalDecimal;

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
    pub participant_instrument: ParticipantInstrumentRef,
    #[serde(default)]
    pub position_side: kairos_primitives::account::PositionSide,
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
            participant_instrument: ParticipantInstrumentRef::new(
                ParticipantRef::new(ParticipantKind::DataProvider, "unknown")
                    .expect("static participant identity"),
                None,
                "UNKNOWN",
            )
            .expect("static provider instrument"),
            position_side: kairos_primitives::account::PositionSide::Net,
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
    /// Provider-native account/product model retained for diagnostics.
    #[serde(default)]
    pub provider_account_model: Option<String>,
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
    pub participant_instrument: ParticipantInstrumentRef,
    pub side: kairos_primitives::execution::OrderSide,
    pub quantity: ExternalDecimal,
    pub filled_quantity: ExternalDecimal,
    pub status: kairos_primitives::integration::OrderStatus,
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
    pub fill_id: kairos_primitives::execution::FillId,
    pub order_id: OrderId,
    pub segment_key: SegmentKey,
    pub participant_instrument: ParticipantInstrumentRef,
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

pub type ExternalAccountEventEnvelope = crate::ExternalEventEnvelope<ExternalAccountEvent>;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalMarketProfileRequest {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub market_id: MarketId,
    pub source_symbol: Symbol,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalMarketProfile {
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub market_id: MarketId,
    pub account_model: Option<ExternalAccountModel>,
    pub margin_mode: Option<String>,
    pub position_mode: Option<String>,
    pub maker_fee: Option<ExternalDecimal>,
    pub taker_fee: Option<ExternalDecimal>,
    pub fee_currency: Option<Currency>,
    pub fee_discount: Option<ExternalDecimal>,
    pub fee_tier: Option<String>,
    pub source: String,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExternalAccountCredentialProfile {
    pub remote_identity: Option<String>,
    pub account_type: Option<String>,
    pub permissions: Vec<String>,
    pub segments: Vec<String>,
    pub attributes: BTreeMap<String, String>,
}
