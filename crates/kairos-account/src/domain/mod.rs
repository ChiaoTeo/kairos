use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

pub mod order;
pub use order::{OrderEvent, OrderRequest, OrderSide, OrderState, OrderStatus, OrderType};

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
pub struct AccountSegment {
    pub identity: ExternalAccountIdentity,
    pub segment_key: String,
    pub environment: String,
    pub account_model: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AccountModel {
    NoMargin,
    Margin,
    Contract,
    ContractUnified,
    Unified,
    PortfolioMargin,
}

impl AccountModel {
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
pub enum MarginMode {
    Cross,
    Isolated,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum PositionMode {
    OneWay,
    Hedge,
}

impl AccountSegment {
    pub fn validate(&self) -> Result<(), String> {
        if self.segment_key.trim().is_empty() || self.environment.trim().is_empty() {
            return Err("segment_key and environment are required".into());
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct DecimalValue {
    pub mantissa: i64,
    pub scale: u8,
}

impl DecimalValue {
    pub const fn new(mantissa: i64, scale: u8) -> Self {
        Self { mantissa, scale }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Balance {
    pub asset_id: String,
    pub asset_code: String,
    pub total: DecimalValue,
    pub available: Option<DecimalValue>,
    pub locked: Option<DecimalValue>,
    pub borrowed: Option<DecimalValue>,
    pub interest: Option<DecimalValue>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Position {
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub quantity: DecimalValue,
    pub average_price: Option<DecimalValue>,
    pub mark_price: Option<DecimalValue>,
    pub unrealized_pnl: Option<DecimalValue>,
    pub realized_pnl: Option<DecimalValue>,
    pub updated_at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AccountStatus {
    Unknown,
    Ready,
    Reconciling,
    TypeMismatch,
    Suspended,
    Unavailable,
}

impl Default for AccountStatus {
    fn default() -> Self {
        Self::Unknown
    }
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

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountState {
    pub balances: BTreeMap<String, Balance>,
    pub collateral: BTreeMap<String, Balance>,
    pub positions: BTreeMap<String, Position>,
    pub open_orders: BTreeMap<String, OpenOrder>,
    pub orders: BTreeMap<String, OrderState>,
    pub status: AccountStatus,
    pub stale: bool,
    pub observed_at_unix_nanos: u64,
    pub generation: u64,
    pub event_sequence: u64,
    pub equity: Option<DecimalValue>,
    pub initial_equity: Option<DecimalValue>,
    pub net_profit: Option<DecimalValue>,
    pub observed_account_model: Option<AccountModel>,
    pub margin_mode: Option<MarginMode>,
    pub position_mode: Option<PositionMode>,
    #[serde(default)]
    pub applied_fill_ids: std::collections::BTreeSet<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Account {
    pub segment: AccountSegment,
    pub state: AccountState,
}

impl Account {
    pub fn new(segment: AccountSegment) -> Result<Self, String> {
        segment.validate()?;
        Ok(Self {
            segment,
            state: AccountState::default(),
        })
    }

    pub fn apply_snapshot(&mut self, snapshot: AccountSnapshot) -> Result<(), String> {
        if snapshot.segment_key != self.segment.segment_key {
            return Err(format!(
                "snapshot segment mismatch: {}",
                snapshot.segment_key
            ));
        }
        let partial = snapshot.partial;
        if partial {
            for balance in snapshot.balances {
                self.state
                    .balances
                    .insert(balance.asset_id.clone(), balance);
            }
            for balance in snapshot.collateral {
                self.state
                    .collateral
                    .insert(balance.asset_id.clone(), balance);
            }
            for position in snapshot.positions {
                if position.quantity.mantissa == 0 {
                    self.state.positions.remove(&position.instrument_id);
                } else {
                    self.state
                        .positions
                        .insert(position.instrument_id.clone(), position);
                }
            }
            for order in snapshot.open_orders {
                self.state.open_orders.insert(order.order_id.clone(), order);
            }
        } else {
            self.state.balances = snapshot
                .balances
                .into_iter()
                .map(|v| (v.asset_id.clone(), v))
                .collect();
            self.state.collateral = snapshot
                .collateral
                .into_iter()
                .map(|v| (v.asset_id.clone(), v))
                .collect();
            self.state.positions = snapshot
                .positions
                .into_iter()
                .map(|v| (v.instrument_id.clone(), v))
                .collect();
            self.state.open_orders = snapshot
                .open_orders
                .into_iter()
                .map(|v| (v.order_id.clone(), v))
                .collect();
        }
        self.state.status = snapshot.status;
        self.state.stale = false;
        self.state.observed_at_unix_nanos = snapshot.observed_at_unix_nanos;
        if !partial || snapshot.equity.is_some() {
            self.state.equity = snapshot.equity;
        }
        if !partial || snapshot.initial_equity.is_some() {
            self.state.initial_equity = snapshot.initial_equity;
        }
        if !partial || snapshot.net_profit.is_some() {
            self.state.net_profit = snapshot.net_profit;
        }
        if !partial || snapshot.account_model.is_some() {
            self.state.observed_account_model = snapshot.account_model;
        }
        if !partial || snapshot.margin_mode.is_some() {
            self.state.margin_mode = snapshot.margin_mode;
        }
        if !partial || snapshot.position_mode.is_some() {
            self.state.position_mode = snapshot.position_mode;
        }
        self.state.generation += 1;
        self.state.event_sequence += 1;
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountSnapshot {
    pub segment_key: String,
    pub balances: Vec<Balance>,
    #[serde(default)]
    pub collateral: Vec<Balance>,
    pub positions: Vec<Position>,
    #[serde(default)]
    pub open_orders: Vec<OpenOrder>,
    pub status: AccountStatus,
    pub observed_at_unix_nanos: u64,
    pub equity: Option<DecimalValue>,
    pub initial_equity: Option<DecimalValue>,
    pub net_profit: Option<DecimalValue>,
    #[serde(default)]
    pub account_model: Option<AccountModel>,
    #[serde(default)]
    pub margin_mode: Option<MarginMode>,
    #[serde(default)]
    pub position_mode: Option<PositionMode>,
    /// REST snapshots replace the projection; stream snapshots are deltas.
    #[serde(default)]
    pub partial: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct OpenOrder {
    pub order_id: String,
    pub venue_order_id: Option<String>,
    pub instrument_id: String,
    pub side: String,
    pub quantity: DecimalValue,
    pub filled_quantity: DecimalValue,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountFill {
    #[serde(default)]
    pub fill_id: Option<String>,
    #[serde(default)]
    pub order_id: Option<String>,
    pub segment_key: String,
    pub instrument_id: String,
    pub quantity: DecimalValue,
    pub price: DecimalValue,
    pub side: FillSide,
    #[serde(default)]
    pub settlement_asset: Option<String>,
    #[serde(default)]
    pub settlement_delta: Option<DecimalValue>,
    #[serde(default)]
    pub fee_asset: Option<String>,
    #[serde(default)]
    pub fee_amount: Option<DecimalValue>,
    pub occurred_at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum FillSide {
    Buy,
    Sell,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AccountEvent {
    Snapshot(AccountSnapshot),
    Fill(AccountFill),
    Order(OrderEvent),
    Batch(Vec<AccountEvent>),
}

impl Account {
    pub fn apply_fill(&mut self, fill: AccountFill) -> Result<(), String> {
        let state_before = self.state.clone();
        match self.apply_fill_unchecked(fill) {
            Ok(()) => Ok(()),
            Err(error) => {
                self.state = state_before;
                Err(error)
            }
        }
    }

    fn apply_fill_unchecked(&mut self, fill: AccountFill) -> Result<(), String> {
        if fill.segment_key != self.segment.segment_key || fill.instrument_id.trim().is_empty() {
            return Err("fill does not belong to this account segment".into());
        }
        if let Some(fill_id) = fill.fill_id.as_deref() {
            if !fill_id.trim().is_empty() && self.state.applied_fill_ids.contains(fill_id) {
                return Err("fill was already applied".into());
            }
        }
        let signed = match fill.side {
            FillSide::Buy => fill.quantity.mantissa,
            FillSide::Sell => -fill.quantity.mantissa,
        };
        let position = self
            .state
            .positions
            .entry(fill.instrument_id.clone())
            .or_insert_with(|| Position {
                instrument_id: fill.instrument_id,
                quantity: DecimalValue::new(0, fill.quantity.scale),
                ..Default::default()
            });
        if position.quantity.scale != fill.quantity.scale {
            return Err("fill and position decimal scales differ".into());
        }
        position.quantity.mantissa = position
            .quantity
            .mantissa
            .checked_add(signed)
            .ok_or_else(|| "position quantity overflow".to_string())?;
        position.average_price = Some(fill.price);
        position.updated_at_unix_nanos = fill.occurred_at_unix_nanos;
        if let (Some(asset), Some(delta)) = (fill.settlement_asset, fill.settlement_delta) {
            apply_balance_delta(&mut self.state.balances, &asset, delta)?;
        }
        if let (Some(asset), Some(amount)) = (fill.fee_asset, fill.fee_amount) {
            if amount.mantissa < 0 {
                return Err("fill fee amount must not be negative".into());
            }
            let delta = DecimalValue::new(
                amount
                    .mantissa
                    .checked_neg()
                    .ok_or_else(|| "fill fee amount overflows".to_string())?,
                amount.scale,
            );
            apply_balance_delta(&mut self.state.balances, &asset, delta)?;
        }
        if let Some(fill_id) = fill.fill_id {
            if !fill_id.trim().is_empty() {
                self.state.applied_fill_ids.insert(fill_id);
            }
        }
        self.state.event_sequence += 1;
        self.state.generation += 1;
        Ok(())
    }

    pub fn plan_order(&mut self, request: OrderRequest, at: u64) -> Result<(), String> {
        if request.account_id != self.segment.identity.account_id
            || request.segment_key != self.segment.segment_key
        {
            return Err("order does not belong to this account segment".into());
        }
        if self.state.orders.contains_key(&request.order_id) {
            return Err("order already exists".into());
        }
        let state = OrderState::new(request, at)?;
        self.state
            .orders
            .insert(state.request.order_id.clone(), state);
        self.state.event_sequence += 1;
        Ok(())
    }

    pub fn apply_order_event(&mut self, event: OrderEvent) -> Result<(), String> {
        let event_status = event.status;
        let state = self
            .state
            .orders
            .get_mut(&event.order_id)
            .ok_or_else(|| "unknown order".to_string())?;
        state.apply(event)?;
        let order = state.clone();
        if event_status.terminal() {
            self.state.open_orders.retain(|key, value| {
                key != &order.request.order_id && value.venue_order_id != order.venue_order_id
            });
        } else {
            self.state.open_orders.insert(
                order.request.order_id.clone(),
                OpenOrder {
                    order_id: order.request.order_id.clone(),
                    venue_order_id: order.venue_order_id.clone(),
                    instrument_id: order.request.instrument_id.clone(),
                    side: match order.request.side {
                        OrderSide::Buy => "buy",
                        OrderSide::Sell => "sell",
                    }
                    .into(),
                    quantity: order.request.quantity,
                    filled_quantity: order.filled_quantity,
                    status: order.status.as_str().into(),
                },
            );
        }
        self.state.event_sequence += 1;
        Ok(())
    }
}

fn apply_balance_delta(
    balances: &mut BTreeMap<String, Balance>,
    asset: &str,
    delta: DecimalValue,
) -> Result<(), String> {
    let asset_code = asset.trim().to_ascii_uppercase();
    if asset_code.is_empty() {
        return Err("fill balance asset is required".into());
    }
    let asset_id = format!("asset:{}", asset_code.to_ascii_lowercase());
    let balance = balances.entry(asset_id.clone()).or_insert_with(|| Balance {
        asset_id,
        asset_code: asset_code.clone(),
        total: DecimalValue::new(0, delta.scale),
        ..Default::default()
    });
    if balance.total.scale != delta.scale {
        return Err("fill balance delta decimal scales differ".into());
    }
    balance.total.mantissa = balance
        .total
        .mantissa
        .checked_add(delta.mantissa)
        .ok_or_else(|| "fill balance delta overflows".to_string())?;
    Ok(())
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AccountView {
    pub account: Account,
}

pub type Accounts = BTreeMap<String, Account>;
