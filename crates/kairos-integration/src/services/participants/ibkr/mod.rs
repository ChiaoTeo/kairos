//! Interactive Brokers TWS/IB Gateway connections.
//!
//! The `ibapi` client is contained in this integration adapter.  Only
//! normalized connection facts cross into the rest of the workspace.

use std::collections::{BTreeMap, HashMap};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use ibapi::accounts::types::AccountId;
use ibapi::accounts::AccountUpdate;
use ibapi::client::sync::Client;
use ibapi::contracts::Contract;
use ibapi::orders::OrderUpdate;
use ibapi::orders::Orders;
use ibapi::subscriptions::Subscription;
use kairos_domain_types::{Currency, FillId, OrderId, Symbol, UnixNanos};

use crate::application::capabilities::account_facts::{
    canonical_account_identity, ExternalAccountSegment, ExternalAccountSnapshot,
    ExternalAccountStatus, ExternalBalance, ExternalDecimal, ExternalOpenOrder, ExternalPosition,
};
use crate::application::capabilities::{
    DecimalValue, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType,
};
use crate::application::{
    AccountEventReceive, AccountEventStreamConnection, AccountReadConnection, CommandOutcome,
    ExternalEventEnvelope, ExternalExecutionEvent, IndeterminateCommand, IntegrationError,
    OrderEntryConnection, OrderEventSource,
};
pub struct IbkrOrderConnection {
    options: IbkrOptions,
}
pub struct IbkrAccountConnection {
    options: IbkrOptions,
}
pub struct IbkrAccountStreamConnection {
    state: crate::domain::ConnectionState,
    options: IbkrOptions,
    account_id: String,
    segment_key: String,
    next_snapshot_poll: Instant,
}
pub struct IbkrExecutionStreamConnection {
    state: crate::domain::ConnectionState,
    options: IbkrOptions,
    account_id: String,
    symbol: Option<String>,
    client: Option<Client>,
    subscription: Option<Subscription<OrderUpdate>>,
    order_symbols: HashMap<i32, String>,
    execution_orders: HashMap<String, i32>,
    execution_accounts: HashMap<String, String>,
    channel_epoch: u64,
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

fn connection_state(
    binding_id: &str,
    domain: &str,
) -> Result<crate::domain::ConnectionState, String> {
    Ok(crate::domain::ConnectionState::new(
        crate::domain::ConnectionDescriptor::new(
            binding_id,
            crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Broker, "ibkr")?,
            domain,
        )?,
    ))
}

impl IbkrOrderConnection {
    pub fn new(options: IbkrOptions) -> Result<Self, String> {
        Ok(Self { options })
    }
}

impl IbkrAccountConnection {
    pub fn new(options: IbkrOptions) -> Result<Self, String> {
        Ok(Self { options })
    }
}

impl IbkrAccountStreamConnection {
    pub fn new(
        options: IbkrOptions,
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            state: connection_state("account.ibkr.equity.stream", "account")?,
            options,
            account_id: account_id.into(),
            segment_key: segment_key.into(),
            next_snapshot_poll: Instant::now(),
        })
    }
}

impl IbkrExecutionStreamConnection {
    pub fn new(
        options: IbkrOptions,
        account_id: impl Into<String>,
        symbol: Option<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            state: connection_state("execution.ibkr.equity.stream", "trading")?,
            options,
            account_id: account_id.into(),
            symbol,
            client: None,
            subscription: None,
            order_symbols: HashMap::new(),
            execution_orders: HashMap::new(),
            execution_accounts: HashMap::new(),
            channel_epoch: 0,
        })
    }

    fn try_next_raw(&mut self) -> Result<Option<ExternalExecutionEvent>, String> {
        let item = self
            .subscription
            .as_ref()
            .and_then(|stream| stream.try_iter_data().next());
        let Some(item) = item else {
            return Ok(None);
        };
        let update = item.map_err(|error| error.to_string())?;
        let event = match update {
            OrderUpdate::OrderStatus(status) => {
                if let Some(account) = self.execution_accounts.get(&status.order_id.to_string()) {
                    if !self.account_id.is_empty() && account != &self.account_id {
                        return Ok(None);
                    }
                }
                ExternalExecutionEvent {
                    order_id: typed_order_id(status.order_id)?,
                    symbol: typed_symbol(
                        self.order_symbols
                            .get(&status.order_id)
                            .cloned()
                            .unwrap_or_else(|| "UNKNOWN".into()),
                    )?,
                    status:
                        crate::application::capabilities::execution_facts::normalize_order_status(
                            &format!("{:?}", status.status),
                        ),
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
                    occurred_at_unix_nanos: now_nanos().into(),
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
                    order_id: typed_order_id(order.order_id)?,
                    symbol: typed_symbol(symbol)?,
                    status:
                        crate::application::capabilities::execution_facts::normalize_order_status(
                            &format!("{:?}", order.order_state.status),
                        ),
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
                    occurred_at_unix_nanos: now_nanos().into(),
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
                    order_id: typed_order_id(value.order_id)?,
                    symbol: typed_symbol(symbol)?,
                    status: kairos_domain_types::OrderStatus::Filled,
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
                    execution_id: Some(typed_fill_id(value.execution_id)?),
                    fee_currency: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: now_nanos().into(),
                    reason: String::new(),
                }
            }
            OrderUpdate::CommissionReport(report) => {
                let Some(order_id) = self.execution_orders.get(&report.execution_id).copied()
                else {
                    return Ok(None);
                };
                ExternalExecutionEvent {
                    order_id: typed_order_id(order_id)?,
                    symbol: typed_symbol(
                        self.order_symbols
                            .get(&order_id)
                            .cloned()
                            .unwrap_or_else(|| "UNKNOWN".into()),
                    )?,
                    status: kairos_domain_types::OrderStatus::Unknown,
                    side: None,
                    order_type: None,
                    quantity: None,
                    limit_price: None,
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: None,
                    fill_price: None,
                    execution_id: Some(typed_fill_id(&report.execution_id)?),
                    fee_currency: Some(typed_currency(report.currency)?),
                    fee_amount: Some(decimal_f64_order(report.commission)),
                    occurred_at_unix_nanos: now_nanos().into(),
                    reason: String::new(),
                }
            }
        };
        Ok(Some(event))
    }
}

impl OrderEntryConnection for IbkrOrderConnection {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        if !matches!(request.order_type, OrderType::Market | OrderType::Limit) {
            return Err(IntegrationError::UnsupportedOperation);
        }
        let limit_price = if request.order_type == OrderType::Limit {
            Some(request.limit_price.ok_or_else(|| {
                IntegrationError::InvalidRequest("IBKR limit price is required".into())
            })?)
        } else {
            None
        };
        let client = self
            .options
            .connect()
            .map_err(IntegrationError::Transport)?;
        let symbol = symbol(request).map_err(IntegrationError::InvalidRequest)?;
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
                    .limit(decimal_f64(limit_price.expect("validated limit price")))
                    .submit(),
                _ => unreachable!("validated order type"),
            },
            OrderSide::Sell => match request.order_type {
                OrderType::Market => builder
                    .sell(decimal_f64(request.quantity))
                    .market()
                    .submit(),
                OrderType::Limit => builder
                    .sell(decimal_f64(request.quantity))
                    .limit(decimal_f64(limit_price.expect("validated limit price")))
                    .submit(),
                _ => unreachable!("validated order type"),
            },
        };
        let order_id = match order_id {
            Ok(order_id) => order_id,
            Err(error) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ))
            }
        };
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            remote_order_id: Some(
                kairos_domain_types::RemoteOrderId::new(order_id.to_string())
                    .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?,
            ),
            filled_quantity: None,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: String::new(),
        }))
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        venue_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let order_id = venue_order_id.parse::<i32>().map_err(|_| {
            IntegrationError::InvalidRequest("IBKR venue order id must be numeric".into())
        })?;
        let client = self
            .options
            .connect()
            .map_err(IntegrationError::Transport)?;
        if let Err(error) = client.cancel_order(order_id, "") {
            return Ok(CommandOutcome::Indeterminate(
                IndeterminateCommand::may_have_been_sent(error.to_string()),
            ));
        }
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            remote_order_id: Some(
                kairos_domain_types::RemoteOrderId::new(venue_order_id)
                    .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?,
            ),
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos.into(),
            reason: String::new(),
        }))
    }
}

impl AccountReadConnection for IbkrAccountConnection {
    fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        fetch_snapshot(&self.options, segment).map_err(IntegrationError::InvalidPayload)
    }
}

impl AccountEventStreamConnection for IbkrAccountStreamConnection {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.mark_ready(true);
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.mark_stopped();
        Ok(())
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.state.health()
    }

    fn recv_account_event(
        &mut self,
        timeout: Duration,
    ) -> Result<AccountEventReceive, IntegrationError> {
        self.connect_channel()?;
        let now = Instant::now();
        if now < self.next_snapshot_poll {
            std::thread::park_timeout(timeout.min(self.next_snapshot_poll - now));
            if Instant::now() < self.next_snapshot_poll {
                return Ok(AccountEventReceive::Idle);
            }
        }
        self.next_snapshot_poll = Instant::now() + Duration::from_secs(1);
        let segment = ExternalAccountSegment {
            identity:
                crate::application::capabilities::account_facts::ExternalAccountIdentity::new(
                    "ibkr",
                    self.account_id.clone(),
                )
                .map_err(IntegrationError::InvalidPayload)?,
            segment_key: kairos_domain_types::SegmentKey::new(self.segment_key.clone())
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            environment: "live".into(),
            account_model: None,
        };
        fetch_snapshot(&self.options, &segment)
            .map(crate::application::ExternalAccountEvent::Snapshot)
            .map(AccountEventReceive::Event)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl OrderEventSource for IbkrExecutionStreamConnection {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.subscription.is_none() {
            let client = match self.options.connect() {
                Ok(client) => client,
                Err(error) => {
                    self.state.mark_failed(error.clone());
                    return Err(IntegrationError::Transport(error));
                }
            };
            let subscription = match client.order_update_stream() {
                Ok(subscription) => subscription,
                Err(error) => {
                    let error = error.to_string();
                    self.state.mark_failed(error.clone());
                    return Err(IntegrationError::Transport(error));
                }
            };
            self.client = Some(client);
            self.subscription = Some(subscription);
            self.channel_epoch = self.channel_epoch.saturating_add(1);
            self.state.mark_ready(true);
        }
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.subscription.take();
        self.client.take();
        self.state.mark_stopped();
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        let reconnect_count = self.state.reconnect_count;
        self.disconnect_channel()?;
        self.connect_channel()?;
        self.state.reconnect_count = reconnect_count.saturating_add(1);
        Ok(())
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.state.health()
    }

    fn try_next_order_event(
        &mut self,
    ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError> {
        self.connect_channel()?;
        let received_at = UnixNanos::from(now_nanos());
        self.try_next_raw()
            .map_err(IntegrationError::Transport)
            .map(|event| {
                event.map(|payload| {
                    let provider_event_id = payload
                        .execution_id
                        .as_ref()
                        .map(ToString::to_string)
                        .or_else(|| {
                            Some(format!(
                                "{}:{:?}:{}",
                                payload.order_id,
                                payload.status,
                                payload.occurred_at_unix_nanos.get()
                            ))
                        });
                    ExternalEventEnvelope {
                        binding_id: self.state.identity.binding_id.clone(),
                        channel_id: "ibkr.order-updates".into(),
                        channel_epoch: self.channel_epoch,
                        provider_event_id,
                        provider_sequence: None,
                        observed_at_unix_nanos: payload.occurred_at_unix_nanos,
                        received_at_unix_nanos: received_at,
                        payload,
                    }
                })
            })
    }
}

fn typed_order_id(value: impl ToString) -> Result<OrderId, String> {
    OrderId::new(format!("ibkr:{}", value.to_string())).map_err(|error| error.to_string())
}

fn typed_symbol(value: String) -> Result<Symbol, String> {
    Symbol::new(value).map_err(|error| error.to_string())
}

fn typed_fill_id(value: impl ToString) -> Result<FillId, String> {
    FillId::new(format!("ibkr:{}", value.to_string())).map_err(|error| error.to_string())
}

fn typed_currency(value: String) -> Result<Currency, String> {
    Currency::new(value).map_err(|error| error.to_string())
}

fn fetch_snapshot(
    options: &IbkrOptions,
    segment: &ExternalAccountSegment,
) -> Result<ExternalAccountSnapshot, String> {
    let client = options.connect()?;
    let account = AccountId(segment.identity.account_id.to_string());
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
                    .is_some_and(|value| value != segment.identity.account_id.as_str())
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
                    .is_some_and(|account| account != segment.identity.account_id.as_str())
                {
                    continue;
                }
                if value.position != 0.0 {
                    let (instrument_id, market_id) = canonical_account_identity(
                        "ibkr-equity",
                        &value.contract.symbol.to_string(),
                    )?;
                    positions.push(ExternalPosition {
                        instrument_id,
                        market_id: Some(market_id),
                        quantity: decimal_f64_value(value.position),
                        average_price: Some(decimal_f64_value(value.average_cost)),
                        mark_price: Some(decimal_f64_value(value.market_price)),
                        unrealized_pnl: Some(decimal_f64_value(value.unrealized_pnl)),
                        realized_pnl: Some(decimal_f64_value(value.realized_pnl)),
                        updated_at_unix_nanos: now_nanos().into(),
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
        .map(
            |(currency, (total, available))| -> Result<ExternalBalance, String> {
                Ok(ExternalBalance {
                    asset_id: kairos_domain_types::AssetId::new(format!(
                        "asset:equity:{currency}"
                    ))?,
                    asset_code: kairos_domain_types::Currency::new(currency)?,
                    total: total.or(available).unwrap_or_default(),
                    available,
                    locked: None,
                    borrowed: None,
                    interest: None,
                })
            },
        )
        .collect::<Result<Vec<_>, _>>()?;
    let open_orders = client
        .open_orders()
        .map_err(|e| e.to_string())?
        .iter_data()
        .filter_map(|item| match item.ok()? {
            Orders::OrderData(value) if value.order.total_quantity > 0.0 => {
                let (instrument_id, _) =
                    canonical_account_identity("ibkr-equity", &value.contract.symbol.to_string())
                        .ok()?;
                Some(ExternalOpenOrder {
                    order_id: kairos_domain_types::OrderId::new(value.order_id.to_string()).ok()?,
                    remote_order_id: Some(
                        kairos_domain_types::RemoteOrderId::new(value.order_id.to_string()).ok()?,
                    ),
                    instrument_id,
                    side: if format!("{:?}", value.order.action).eq_ignore_ascii_case("sell") {
                        kairos_domain_types::OrderSide::Sell
                    } else {
                        kairos_domain_types::OrderSide::Buy
                    },
                    quantity: decimal_f64_value(value.order.total_quantity),
                    filled_quantity: ExternalDecimal::new(0, 0),
                    status:
                        crate::application::capabilities::execution_facts::normalize_order_status(
                            &format!("{:?}", value.order_state.status),
                        ),
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
        observed_at_unix_nanos: now_nanos().into(),
        equity,
        initial_equity: None,
        net_profit,
        account_model: segment
            .account_model
            .as_deref()
            .and_then(crate::application::capabilities::ExternalAccountModel::parse),
        margin_mode: None,
        position_mode: None,
        partial: false,
    })
}

fn symbol(request: &OrderEntryRequest) -> Result<String, String> {
    let value = request.market_id.as_deref().ok_or_else(|| {
        "IBKR equity order requires a market_id resolved by Reference".to_string()
    })?;
    let value = value.rsplit(':').next().unwrap_or(value).trim();
    if value.is_empty() {
        return Err("IBKR equity symbol is required".into());
    }
    Ok(value.to_ascii_uppercase())
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
    ExternalDecimal::parse(value)
}
fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
