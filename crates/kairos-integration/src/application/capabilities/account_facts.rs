//! Provider-neutral facts produced by private account connections.
//!
//! These are deliberately external facts, not account-domain entities.  The
//! account module owns the mapping from these values into its state model.

use serde::{Deserialize, Serialize};

use kairos_domain_types::{
    AccountId, AssetId, Currency, InstrumentId, MarketId, OrderId, RemoteOrderId, SegmentKey,
    UnixNanos,
};

/// Convert a provider symbol into a canonical identity only when the product
/// grammar supplies enough economic information. Provider symbols themselves
/// never become Instrument IDs.
pub fn canonical_account_identity(
    product: &str,
    provider_symbol: &str,
) -> Result<(InstrumentId, MarketId), String> {
    let symbol = provider_symbol.trim().to_ascii_uppercase();
    if symbol.is_empty() {
        return Err("provider symbol is empty".into());
    }
    match product {
        "binance-spot" => {
            let (base, _quote) = split_pair(&symbol)?;
            Ok((
                InstrumentId::spot(base.clone()).map_err(|e| e.to_string())?,
                MarketId::spot(
                    &kairos_domain_types::Exchange::new("exchange:binance")
                        .map_err(|e| e.to_string())?,
                    &symbol,
                )
                .map_err(|e| e.to_string())?,
            ))
        }
        "binance-futures" => {
            let (base, quote) = split_pair(&symbol)?;
            Ok((
                InstrumentId::new(format!("instrument:perpetual:{base}-{quote}"))
                    .map_err(|e| e.to_string())?,
                MarketId::new(format!("market:binance:perpetual:{symbol}"))
                    .map_err(|e| e.to_string())?,
            ))
        }
        "binance-options" => {
            let parts: Vec<_> = symbol.split('-').collect();
            if parts.len() != 4 || !matches!(parts[3], "C" | "P") {
                return Err(format!("unsupported Binance option symbol: {symbol}"));
            }
            let expiry = parts[1];
            if expiry.len() != 6 || !expiry.chars().all(|c| c.is_ascii_digit()) {
                return Err(format!("option symbol has invalid expiry: {symbol}"));
            }
            let instrument = InstrumentId::new(format!(
                "instrument:option:{}-USDT:20{}:{}:{}",
                parts[0], expiry, parts[2], parts[3]
            ))
            .map_err(|e| e.to_string())?;
            let market = MarketId::new(format!("market:binance:options:{symbol}"))
                .map_err(|e| e.to_string())?;
            Ok((instrument, market))
        }
        "okx" => {
            let parts: Vec<_> = symbol.split('-').collect();
            match parts.as_slice() {
                [base, _quote] => Ok((
                    InstrumentId::spot(base).map_err(|e| e.to_string())?,
                    MarketId::new(format!("market:okx:spot:{symbol}"))
                        .map_err(|e| e.to_string())?,
                )),
                [base, quote, "SWAP"] => Ok((
                    InstrumentId::new(format!("instrument:perpetual:{base}-{quote}"))
                        .map_err(|e| e.to_string())?,
                    MarketId::new(format!("market:okx:swap:{symbol}"))
                        .map_err(|e| e.to_string())?,
                )),
                _ => Err(format!("unsupported OKX symbol: {symbol}")),
            }
        }
        "ibkr-equity" => Ok((
            InstrumentId::new(format!("instrument:equity:US:{symbol}:common"))
                .map_err(|e| e.to_string())?,
            MarketId::new(format!("market:ibkr:equity:{symbol}")).map_err(|e| e.to_string())?,
        )),
        _ => Err(format!("no canonical account symbol mapping for {product}")),
    }
}

fn split_pair(symbol: &str) -> Result<(String, String), String> {
    const QUOTES: [&str; 8] = ["USDT", "USDC", "BUSD", "FDUSD", "USD", "BTC", "ETH", "BNB"];
    let quote = QUOTES
        .iter()
        .find(|quote| symbol.ends_with(**quote))
        .ok_or_else(|| format!("cannot resolve quote asset in provider symbol: {symbol}"))?;
    let base = symbol
        .strip_suffix(quote)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("cannot resolve base asset in provider symbol: {symbol}"))?;
    Ok((base.to_owned(), (*quote).to_owned()))
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
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
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
            instrument_id: InstrumentId::new("instrument:unknown")
                .expect("static instrument identity is valid"),
            market_id: None,
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
    pub instrument_id: InstrumentId,
    pub side: kairos_domain_types::OrderSide,
    pub quantity: ExternalDecimal,
    pub filled_quantity: ExternalDecimal,
    pub status: kairos_domain_types::OrderStatus,
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
    pub fill_id: kairos_domain_types::FillId,
    pub order_id: OrderId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
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
