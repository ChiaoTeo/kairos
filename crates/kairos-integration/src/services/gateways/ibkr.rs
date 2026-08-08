//! Interactive Brokers TWS/IB Gateway connections.
//!
//! The `ibapi` client is contained in this integration adapter.  Only
//! normalized connection facts cross into the rest of the workspace.

use std::collections::{BTreeMap, HashMap};
use std::time::{SystemTime, UNIX_EPOCH};

use ibapi::accounts::types::AccountId;
use ibapi::accounts::AccountUpdate;
use ibapi::client::sync::Client;
use ibapi::contracts::Contract;
use ibapi::orders::OrderUpdate;
use ibapi::orders::Orders;
use ibapi::subscriptions::Subscription;

use crate::application::{
    AccountEventStreamConnection, AccountReadConnection, Connection, ConnectionSpec,
    ExecutionStreamConnection, ExternalExecutionEvent, IntegrationError, OrderEntryConnection,
};
use crate::domain::account::{
    ExternalAccountSegment, ExternalAccountSnapshot, ExternalAccountStatus, ExternalBalance,
    ExternalDecimal, ExternalOpenOrder, ExternalPosition,
};
use crate::domain::{
    AccessScope, DecimalValue, IntegrationCapability, OrderEntryEvent, OrderEntryRequest,
    OrderEntryStatus, OrderSide, OrderType, ProductFamily, TransportKind,
};
use crate::services::connections::ManagedConnection;

pub struct IbkrOrderConnection {
    connection: ManagedConnection,
    options: IbkrOptions,
}
pub struct IbkrAccountConnection {
    connection: ManagedConnection,
    options: IbkrOptions,
}
pub struct IbkrAccountStreamConnection {
    connection: ManagedConnection,
    options: IbkrOptions,
    account_id: String,
    segment_key: String,
}
pub struct IbkrExecutionStreamConnection {
    connection: ManagedConnection,
    options: IbkrOptions,
    account_id: String,
    symbol: Option<String>,
    client: Option<Client>,
    subscription: Option<Subscription<OrderUpdate>>,
    order_symbols: HashMap<i32, String>,
    execution_orders: HashMap<String, i32>,
    execution_accounts: HashMap<String, String>,
}

#[derive(Clone, Debug)]
pub struct IbkrOptions {
    pub host: String,
    pub port: u16,
    pub client_id: i32,
}

impl IbkrOptions {
    pub fn new(host: impl Into<String>, port: u16, client_id: i32) -> Result<Self, String> {
        let host = host.into();
        if host.trim().is_empty() || port == 0 {
            return Err("IBKR host and port are required".into());
        }
        Ok(Self {
            host,
            port,
            client_id,
        })
    }
    fn address(&self) -> String {
        format!("{}:{}", self.host, self.port)
    }
    fn connect(&self) -> Result<Client, String> {
        Client::connect(&self.address(), self.client_id).map_err(|e| e.to_string())
    }
}

fn connection(spec: ConnectionSpec) -> Result<ManagedConnection, String> {
    ManagedConnection::new(spec, Vec::new())
}

impl IbkrOrderConnection {
    pub fn new(options: IbkrOptions) -> Result<Self, String> {
        let spec = ConnectionSpec {
            connection_id: "execution.ibkr.equity.rest".into(),
            route: crate::domain::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::OrderEntry,
            credential_id: Some("ibkr".into()),
            asset_type: Some(crate::domain::AssetType::Equity),
        };
        Ok(Self {
            connection: connection(spec)?,
            options,
        })
    }
}
impl Connection for IbkrOrderConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.start()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}
impl OrderEntryConnection for IbkrOrderConnection {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        self.start()?;
        let client = self.options.connect()?;
        let symbol = symbol(request)?;
        let contract = Contract::stock(&symbol)
            .on_exchange("SMART")
            .in_currency("USD")
            .build();
        let builder = client.order(&contract);
        let order_id = match request.side {
            OrderSide::Buy => match request.order_type {
                OrderType::Market => builder.buy(decimal_f64(request.quantity)).market().submit(),
                OrderType::Limit => builder
                    .buy(decimal_f64(request.quantity))
                    .limit(decimal_f64(
                        request.limit_price.ok_or("IBKR limit price is required")?,
                    ))
                    .submit(),
                _ => Err(ibapi::Error::InvalidArgument(
                    "IBKR supports market and limit orders".into(),
                )),
            },
            OrderSide::Sell => match request.order_type {
                OrderType::Market => builder
                    .sell(decimal_f64(request.quantity))
                    .market()
                    .submit(),
                OrderType::Limit => builder
                    .sell(decimal_f64(request.quantity))
                    .limit(decimal_f64(
                        request.limit_price.ok_or("IBKR limit price is required")?,
                    ))
                    .submit(),
                _ => Err(ibapi::Error::InvalidArgument(
                    "IBKR supports market and limit orders".into(),
                )),
            },
        }
        .map_err(|e| e.to_string())?;
        Ok(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            venue_order_id: Some(order_id.to_string()),
            filled_quantity: None,
            occurred_at_unix_nanos: now_nanos(),
            reason: String::new(),
        })
    }
    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        venue_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<OrderEntryEvent, String> {
        self.start()?;
        let client = self.options.connect()?;
        let order_id = venue_order_id
            .parse::<i32>()
            .map_err(|_| "IBKR venue order id must be numeric".to_string())?;
        let _ = client
            .cancel_order(order_id, "")
            .map_err(|e| e.to_string())?;
        Ok(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            venue_order_id: Some(venue_order_id.into()),
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos,
            reason: String::new(),
        })
    }
}

impl IbkrAccountConnection {
    pub fn new(options: IbkrOptions) -> Result<Self, String> {
        let spec = ConnectionSpec {
            connection_id: "account.ibkr.equity.rest".into(),
            route: crate::domain::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountRead,
            credential_id: Some("ibkr".into()),
            asset_type: Some(crate::domain::AssetType::Equity),
        };
        Ok(Self {
            connection: connection(spec)?,
            options,
        })
    }
}
impl Connection for IbkrAccountConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.start()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}
impl AccountReadConnection for IbkrAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        fetch_snapshot(&self.options, segment).map_err(IntegrationError::InvalidPayload)
    }
}

impl IbkrAccountStreamConnection {
    pub fn new(
        options: IbkrOptions,
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, String> {
        let spec = ConnectionSpec {
            connection_id: "account.ibkr.equity.stream".into(),
            route: crate::domain::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountStream,
            credential_id: Some("ibkr".into()),
            asset_type: Some(crate::domain::AssetType::Equity),
        };
        Ok(Self {
            connection: connection(spec)?,
            options,
            account_id: account_id.into(),
            segment_key: segment_key.into(),
        })
    }
}
impl Connection for IbkrAccountStreamConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.start()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}
impl AccountEventStreamConnection for IbkrAccountStreamConnection {
    fn next_account_event(
        &mut self,
    ) -> Result<Option<crate::domain::ExternalAccountEvent>, IntegrationError> {
        self.start().map_err(IntegrationError::Transport)?;
        let segment = ExternalAccountSegment {
            identity: crate::domain::account::ExternalAccountIdentity::new(
                "ibkr",
                self.account_id.clone(),
            )
            .map_err(IntegrationError::InvalidPayload)?,
            segment_key: self.segment_key.clone(),
            environment: "live".into(),
            account_model: None,
        };
        fetch_snapshot(&self.options, &segment)
            .map(crate::domain::ExternalAccountEvent::Snapshot)
            .map(Some)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl IbkrExecutionStreamConnection {
    pub fn new(
        options: IbkrOptions,
        account_id: impl Into<String>,
        symbol: Option<String>,
    ) -> Result<Self, String> {
        let spec = ConnectionSpec {
            connection_id: "execution.ibkr.equity.stream".into(),
            route: crate::domain::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::ExecutionStream,
            credential_id: Some("ibkr".into()),
            asset_type: Some(crate::domain::AssetType::Equity),
        };
        Ok(Self {
            connection: connection(spec)?,
            options,
            account_id: account_id.into(),
            symbol,
            client: None,
            subscription: None,
            order_symbols: HashMap::new(),
            execution_orders: HashMap::new(),
            execution_accounts: HashMap::new(),
        })
    }
}
impl Connection for IbkrExecutionStreamConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.subscription = None;
        self.client = None;
        self.order_symbols.clear();
        self.execution_orders.clear();
        self.execution_accounts.clear();
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.stop()?;
        self.start()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}
impl ExecutionStreamConnection for IbkrExecutionStreamConnection {
    fn next_execution_event(&mut self) -> Result<Option<ExternalExecutionEvent>, String> {
        self.start()?;
        if self.subscription.is_none() {
            let client = self.options.connect()?;
            let subscription = client.order_update_stream().map_err(|e| e.to_string())?;
            self.client = Some(client);
            self.subscription = Some(subscription);
        }
        let item = self
            .subscription
            .as_ref()
            .and_then(|stream| stream.try_iter_data().next());
        let Some(item) = item else {
            return Ok(None);
        };
        let update = item.map_err(|e| e.to_string())?;
        let event = match update {
            OrderUpdate::OrderStatus(status) => {
                if let Some(account) = self.execution_accounts.get(&status.order_id.to_string()) {
                    if !self.account_id.is_empty() && account != &self.account_id {
                        return Ok(None);
                    }
                }
                ExternalExecutionEvent {
                    order_id: status.order_id.to_string(),
                    symbol: self
                        .order_symbols
                        .get(&status.order_id)
                        .cloned()
                        .unwrap_or_else(|| "UNKNOWN".into()),
                    status: format!("{:?}", status.status),
                    side: None,
                    order_type: None,
                    quantity: None,
                    limit_price: None,
                    filled_quantity: Some(decimal_f64_order(status.filled)),
                    remaining_quantity: Some(decimal_f64_order(status.remaining)),
                    fill_quantity: None,
                    fill_price: status.last_fill_price.map(decimal_f64_order),
                    execution_id: None,
                    fee_currency: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: now_nanos(),
                    reason: String::new(),
                }
            }
            OrderUpdate::OpenOrder(order) => {
                let symbol = order.contract.symbol.to_string();
                if self.symbol.as_deref().is_some_and(|value| value != symbol) {
                    return Ok(None);
                }
                if !self.account_id.is_empty()
                    && !order.order.account.is_empty()
                    && order.order.account != self.account_id
                {
                    return Ok(None);
                }
                self.order_symbols.insert(order.order_id, symbol.clone());
                ExternalExecutionEvent {
                    order_id: order.order_id.to_string(),
                    symbol,
                    status: format!("{:?}", order.order_state.status),
                    side: Some(
                        if format!("{:?}", order.order.action).eq_ignore_ascii_case("sell") {
                            OrderSide::Sell
                        } else {
                            OrderSide::Buy
                        },
                    ),
                    order_type: Some(if order.order.order_type.eq_ignore_ascii_case("MKT") {
                        OrderType::Market
                    } else {
                        OrderType::Limit
                    }),
                    quantity: Some(decimal_f64_order(order.order.total_quantity)),
                    limit_price: order
                        .order
                        .limit_price
                        .filter(|value| *value > 0.0)
                        .map(decimal_f64_order),
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: None,
                    fill_price: None,
                    execution_id: None,
                    fee_currency: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: now_nanos(),
                    reason: String::new(),
                }
            }
            OrderUpdate::ExecutionData(execution) => {
                let value = execution.execution;
                let symbol = execution.contract.symbol.to_string();
                if self
                    .symbol
                    .as_deref()
                    .is_some_and(|filter| filter != symbol)
                {
                    return Ok(None);
                }
                if !self.account_id.is_empty()
                    && !value.account_number.is_empty()
                    && value.account_number != self.account_id
                {
                    return Ok(None);
                }
                self.order_symbols.insert(value.order_id, symbol.clone());
                self.execution_orders
                    .insert(value.execution_id.clone(), value.order_id);
                self.execution_accounts
                    .insert(value.order_id.to_string(), value.account_number.clone());
                ExternalExecutionEvent {
                    order_id: value.order_id.to_string(),
                    symbol,
                    status: "filled".into(),
                    side: Some(
                        if format!("{:?}", value.side).eq_ignore_ascii_case("sold") {
                            OrderSide::Sell
                        } else {
                            OrderSide::Buy
                        },
                    ),
                    order_type: None,
                    quantity: Some(decimal_f64_order(value.shares)),
                    limit_price: None,
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: Some(decimal_f64_order(value.shares)),
                    fill_price: Some(decimal_f64_order(value.price)),
                    execution_id: Some(value.execution_id),
                    fee_currency: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: now_nanos(),
                    reason: String::new(),
                }
            }
            OrderUpdate::CommissionReport(report) => {
                let Some(order_id) = self.execution_orders.get(&report.execution_id).copied()
                else {
                    return Ok(None);
                };
                ExternalExecutionEvent {
                    order_id: order_id.to_string(),
                    symbol: self
                        .order_symbols
                        .get(&order_id)
                        .cloned()
                        .unwrap_or_else(|| "UNKNOWN".into()),
                    status: "commission".into(),
                    side: None,
                    order_type: None,
                    quantity: None,
                    limit_price: None,
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: None,
                    fill_price: None,
                    execution_id: Some(report.execution_id),
                    fee_currency: Some(report.currency),
                    fee_amount: Some(decimal_f64_order(report.commission)),
                    occurred_at_unix_nanos: now_nanos(),
                    reason: String::new(),
                }
            }
        };
        Ok(Some(event))
    }
}

fn fetch_snapshot(
    options: &IbkrOptions,
    segment: &ExternalAccountSegment,
) -> Result<ExternalAccountSnapshot, String> {
    let client = options.connect()?;
    let account = AccountId(segment.identity.account_id.clone());
    let updates = client
        .account_updates(&account)
        .map_err(|e| e.to_string())?;
    let mut balance_values: BTreeMap<String, (Option<ExternalDecimal>, Option<ExternalDecimal>)> =
        BTreeMap::new();
    let mut positions = Vec::new();
    let mut equity = None;
    let mut net_profit = None;
    for item in updates.iter_data() {
        match item.map_err(|e| e.to_string())? {
            AccountUpdate::AccountValue(value) => {
                if value
                    .account
                    .as_deref()
                    .is_some_and(|value| value != segment.identity.account_id)
                {
                    continue;
                }
                if let Ok(number) = decimal_text(&value.value) {
                    match value.key.as_str() {
                        "TotalCashValue" => {
                            balance_values.entry(value.currency).or_default().0 = Some(number);
                        }
                        "AvailableFunds" => {
                            balance_values.entry(value.currency).or_default().1 = Some(number);
                        }
                        "NetLiquidation" => equity = Some(number),
                        "RealizedPnL" | "RealizedPnL-S" => net_profit = Some(number),
                        _ => {}
                    }
                }
            }
            AccountUpdate::PortfolioValue(value) => {
                if value
                    .account
                    .as_deref()
                    .is_some_and(|account| account != segment.identity.account_id)
                {
                    continue;
                }
                if value.position != 0.0 {
                    positions.push(ExternalPosition {
                        instrument_id: format!("instrument:ibkr:{}", value.contract.symbol),
                        market_id: Some(format!("market:ibkr:{}", value.contract.symbol)),
                        quantity: decimal_f64_value(value.position),
                        average_price: Some(decimal_f64_value(value.average_cost)),
                        mark_price: Some(decimal_f64_value(value.market_price)),
                        unrealized_pnl: Some(decimal_f64_value(value.unrealized_pnl)),
                        realized_pnl: Some(decimal_f64_value(value.realized_pnl)),
                        updated_at_unix_nanos: now_nanos(),
                    });
                }
            }
            AccountUpdate::End => {
                updates.cancel();
                break;
            }
            AccountUpdate::UpdateTime(_) => {}
        }
    }
    let balances = balance_values
        .into_iter()
        .map(|(currency, (total, available))| ExternalBalance {
            asset_id: format!("asset:equity:{currency}"),
            asset_code: currency,
            total: total.or(available).unwrap_or_default(),
            available,
            locked: None,
            borrowed: None,
            interest: None,
        })
        .collect();
    let open_orders = client
        .open_orders()
        .map_err(|e| e.to_string())?
        .iter_data()
        .filter_map(|item| match item.ok()? {
            Orders::OrderData(value) if value.order.total_quantity > 0.0 => {
                Some(ExternalOpenOrder {
                    order_id: value.order_id.to_string(),
                    venue_order_id: Some(value.order_id.to_string()),
                    instrument_id: format!("instrument:ibkr:{}", value.contract.symbol),
                    side: format!("{:?}", value.order.action).to_ascii_lowercase(),
                    quantity: decimal_f64_value(value.order.total_quantity),
                    filled_quantity: ExternalDecimal::new(0, 0),
                    status: format!("{:?}", value.order_state.status),
                })
            }
            _ => None,
        })
        .collect();
    Ok(ExternalAccountSnapshot {
        segment_key: segment.segment_key.clone(),
        balances,
        collateral: Vec::new(),
        positions,
        open_orders,
        status: ExternalAccountStatus::Ready,
        observed_at_unix_nanos: now_nanos(),
        equity,
        initial_equity: None,
        net_profit,
        account_model: segment
            .account_model
            .as_deref()
            .and_then(crate::domain::ExternalAccountModel::parse),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    request
        .market_id
        .as_deref()
        .or_else(|| request.instrument_id.strip_prefix("instrument:ibkr:"))
        .or_else(|| request.instrument_id.strip_prefix("instrument:equity:"))
        .map(|v| {
            v.rsplit(':')
                .next()
                .unwrap_or(v)
                .trim()
                .to_ascii_uppercase()
        })
        .filter(|v| !v.is_empty())
        .ok_or_else(|| "IBKR equity symbol is required".into())
}
fn decimal_f64(value: DecimalValue) -> f64 {
    let factor = 10_f64.powi(value.scale as i32);
    value.mantissa as f64 / factor
}
fn decimal_f64_order(value: f64) -> DecimalValue {
    let external = decimal_f64_value(value);
    DecimalValue::new(external.mantissa, external.scale)
}
fn decimal_f64_value(value: f64) -> ExternalDecimal {
    let text = format!("{value:.8}");
    decimal_text(&text).unwrap_or_default()
}
fn decimal_text(value: &str) -> Result<ExternalDecimal, String> {
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
    let mantissa = format!("{whole}{fraction}")
        .parse::<i64>()
        .map_err(|_| format!("invalid IBKR decimal: {value}"))?;
    Ok(ExternalDecimal::new(
        if negative { -mantissa } else { mantissa },
        fraction.len() as u8,
    ))
}
fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
