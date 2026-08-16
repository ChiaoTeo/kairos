//! Native async IBKR execution session and capability projections.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use futures_util::StreamExt;
use ibapi::contracts::Contract;
use ibapi::orders::{OrderData, OrderUpdate, Orders};
use ibapi::subscriptions::{Subscription, SubscriptionItemStreamExt};
use ibapi::Client;
use kairos_primitives::{
    ClientOrderId, Currency, FillId, OrderId, RemoteOrderId, Symbol, UnixNanos,
};
use tokio::sync::{watch, Mutex};

use crate::application::capabilities::{
    DecimalValue, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderSide, OrderType,
};
use crate::application::{
    CommandOutcome, CommandResult, ConnectionDescriptor, ExternalEventEnvelope,
    ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery, IndeterminateCommand,
    IntegrationError,
};
use crate::domain::{ConnectionHealth, ConnectionLifecycle};

use super::{normalize_ibkr_order_status, IbkrOptions};

const CONNECTION_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);
const QUERY_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);
const COMMAND_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);

pub(crate) struct IbkrAsyncSession {
    options: IbkrOptions,
    account_id: String,
    client: Mutex<Option<Arc<Client>>>,
    notices: watch::Sender<Option<ibapi::Notice>>,
    next_order_id: Mutex<Option<i32>>,
    order_metadata: Mutex<HashMap<i32, (String, String)>>,
}

impl IbkrAsyncSession {
    pub(crate) fn new(options: IbkrOptions, account_id: String) -> Arc<Self> {
        let (notices, _) = watch::channel(None);
        Arc::new(Self {
            options,
            account_id,
            client: Mutex::new(None),
            notices,
            next_order_id: Mutex::new(None),
            order_metadata: Mutex::new(HashMap::new()),
        })
    }

    pub(super) async fn client(&self) -> Result<Arc<Client>, IntegrationError> {
        let mut slot = self.client.lock().await;
        if let Some(client) = slot.as_ref().filter(|client| client.is_connected()) {
            return Ok(client.clone());
        }
        let address = format!("{}:{}", self.options.host, self.options.port);
        let (client, mut notices) = tokio::time::timeout(
            CONNECTION_TIMEOUT,
            Client::builder()
                .address(address)
                .client_id(self.options.client_id)
                .connect_with_notice_stream(),
        )
        .await
        .map_err(|_| IntegrationError::Transport("IBKR connection timed out".into()))?
        .map_err(|error| IntegrationError::Transport(error.to_string()))?;
        self.notices.send_replace(None);
        let notice_sender = self.notices.clone();
        tokio::spawn(async move {
            while let Some(notice) = notices.next().await {
                observe_notice(&notice, "session");
                notice_sender.send_replace(Some(notice));
            }
            tracing::warn!(
                event = "ibkr_notice_stream_ended",
                component = "integration",
                "IBKR global notice stream ended"
            );
        });
        let client = Arc::new(client);
        let (provider_next, highest_open) = tokio::time::timeout(QUERY_TIMEOUT, async {
            if !self.account_id.is_empty() {
                let accounts = client
                    .managed_accounts()
                    .await
                    .map_err(|error| IntegrationError::Authentication(error.to_string()))?;
                if !accounts.iter().any(|account| account == &self.account_id) {
                    return Err(IntegrationError::Authentication(format!(
                        "IBKR target account {} is not managed by client id {}",
                        self.account_id, self.options.client_id
                    )));
                }
            }
            let provider_next = client
                .next_valid_order_id()
                .await
                .map_err(|error| IntegrationError::Transport(error.to_string()))?;
            let open_orders =
                collect_orders(client.open_orders().await.map_err(transport)?).await?;
            let highest_open = open_orders.iter().map(|order| order.order_id).max();
            let mut metadata = self.order_metadata.lock().await;
            for order in open_orders {
                metadata.insert(
                    order.order_id,
                    (order.contract.symbol.to_string(), order.order.account),
                );
            }
            Ok::<_, IntegrationError>((provider_next, highest_open))
        })
        .await
        .map_err(|_| IntegrationError::Unavailable("IBKR readiness query timed out".into()))??;
        *self.next_order_id.lock().await = Some(highest_open.map_or(provider_next, |value| {
            provider_next.max(value.saturating_add(1))
        }));
        *slot = Some(client.clone());
        Ok(client)
    }

    pub(super) fn notice_receiver(&self) -> watch::Receiver<Option<ibapi::Notice>> {
        self.notices.subscribe()
    }

    async fn allocate_order_id(&self, client: &Client) -> Result<i32, IntegrationError> {
        let mut next = self.next_order_id.lock().await;
        if next.is_none() {
            let provider_next = client
                .next_valid_order_id()
                .await
                .map_err(|error| IntegrationError::Transport(error.to_string()))?;
            let highest_open = collect_orders(client.open_orders().await.map_err(transport)?)
                .await?
                .into_iter()
                .map(|order| order.order_id)
                .max();
            *next = Some(highest_open.map_or(provider_next, |value| provider_next.max(value + 1)));
        }
        let allocated = next.expect("IBKR order id initialized");
        *next = Some(allocated.checked_add(1).ok_or_else(|| {
            IntegrationError::Unavailable("IBKR order id space exhausted".into())
        })?);
        Ok(allocated)
    }

    async fn remember_order(&self, order_id: i32, symbol: String, account_id: String) {
        self.order_metadata
            .lock()
            .await
            .insert(order_id, (symbol, account_id));
    }

    async fn order_metadata(&self, order_id: i32) -> Option<(String, String)> {
        self.order_metadata.lock().await.get(&order_id).cloned()
    }
}

pub(crate) struct IbkrAsyncOrderEntry {
    pub(crate) session: Arc<IbkrAsyncSession>,
}

impl IbkrAsyncOrderEntry {
    pub(crate) async fn submit(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        if !matches!(request.order_type, OrderType::Market | OrderType::Limit) {
            return Err(IntegrationError::UnsupportedOperation);
        }
        let symbol = request.provider_instrument.source_symbol.as_str();
        let contract = Contract::stock(symbol)
            .on_exchange("SMART")
            .in_currency("USD")
            .build();
        let client = self.session.client().await?;
        let quantity = decimal_f64(request.quantity);
        let builder = match request.side {
            OrderSide::Buy => client.order(&contract).buy(quantity),
            OrderSide::Sell => client.order(&contract).sell(quantity),
        };
        let builder = match request.order_type {
            OrderType::Market => builder.market(),
            OrderType::Limit => {
                builder.limit(decimal_f64(request.limit_price.ok_or_else(|| {
                    IntegrationError::InvalidRequest("IBKR limit price is required".into())
                })?))
            }
            _ => unreachable!("validated order type"),
        };
        let mut order = builder
            .account(request.account_id.to_string())
            .build()
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        order.order_ref = request.order_id.to_string();
        let order_id = self.session.allocate_order_id(&client).await?;
        self.session
            .remember_order(order_id, symbol.to_owned(), request.account_id.to_string())
            .await;
        match tokio::time::timeout(
            COMMAND_TIMEOUT,
            client.submit_order(order_id, &contract, &order),
        )
        .await
        {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ))
            }
            Err(_) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent("IBKR submit timed out"),
                ))
            }
        }
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            remote_order_id: Some(
                RemoteOrderId::new(format!("ibkr:{order_id}"))
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            ),
            filled_quantity: None,
            occurred_at_unix_nanos: now_nanos().into(),
            reason: String::new(),
        }))
    }

    pub(crate) async fn cancel(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        let order_id = parse_remote_order_id(remote_order_id)?;
        let client = self.session.client().await?;
        match tokio::time::timeout(COMMAND_TIMEOUT, client.cancel_order(order_id, "")).await {
            Ok(Ok(_subscription)) => {}
            Ok(Err(error)) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ))
            }
            Err(_) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent("IBKR cancel timed out"),
                ))
            }
        }
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            remote_order_id: Some(
                RemoteOrderId::new(remote_order_id)
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
            ),
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos.into(),
            reason: String::new(),
        }))
    }
}

pub(crate) struct IbkrAsyncOrderQuery {
    pub(crate) session: Arc<IbkrAsyncSession>,
    pub(crate) descriptor: ConnectionDescriptor,
    pub(crate) account_id: String,
}

impl IbkrAsyncOrderQuery {
    pub(crate) async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let client = self.session.client().await?;
        let rows = tokio::time::timeout(QUERY_TIMEOUT, async {
            collect_orders(client.open_orders().await.map_err(transport)?).await
        })
        .await
        .map_err(|_| IntegrationError::Unavailable("IBKR open-orders query timed out".into()))??;
        normalize_orders(rows, &self.descriptor.binding_id, &self.account_id, query)
    }

    pub(crate) async fn history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let client = self.session.client().await?;
        let rows = tokio::time::timeout(QUERY_TIMEOUT, async {
            collect_orders(client.completed_orders(true).await.map_err(transport)?).await
        })
        .await
        .map_err(|_| {
            IntegrationError::Unavailable("IBKR order-history query timed out".into())
        })??;
        normalize_orders(rows, &self.descriptor.binding_id, &self.account_id, query)
    }
}

pub(crate) struct IbkrAsyncOrderEvents {
    pub(crate) session: Arc<IbkrAsyncSession>,
    pub(crate) descriptor: ConnectionDescriptor,
    pub(crate) account_id: String,
    pub(crate) symbol: Option<String>,
    subscription: Option<Subscription<OrderUpdate>>,
    notices: watch::Receiver<Option<ibapi::Notice>>,
    lifecycle: ConnectionLifecycle,
    last_error: Option<String>,
    channel_epoch: u64,
    order_symbols: HashMap<i32, String>,
    execution_orders: HashMap<String, i32>,
    execution_accounts: HashMap<i32, String>,
}

impl IbkrAsyncOrderEvents {
    pub(crate) fn new(
        session: Arc<IbkrAsyncSession>,
        descriptor: ConnectionDescriptor,
        account_id: String,
        symbol: Option<String>,
    ) -> Self {
        let notices = session.notice_receiver();
        Self {
            session,
            descriptor,
            account_id,
            symbol,
            subscription: None,
            notices,
            lifecycle: ConnectionLifecycle::Created,
            last_error: None,
            channel_epoch: 0,
            order_symbols: HashMap::new(),
            execution_orders: HashMap::new(),
            execution_accounts: HashMap::new(),
        }
    }

    pub(crate) async fn connect(&mut self) -> Result<(), IntegrationError> {
        if self.subscription.is_some() && self.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        let client = self.session.client().await?;
        match tokio::time::timeout(QUERY_TIMEOUT, client.order_update_stream()).await {
            Err(_) => {
                let error =
                    IntegrationError::Unavailable("IBKR order-event subscription timed out".into());
                self.lifecycle = ConnectionLifecycle::Degraded;
                self.last_error = Some(error.to_string());
                Err(error)
            }
            Ok(Ok(subscription)) => {
                self.subscription = Some(subscription);
                self.channel_epoch = self.channel_epoch.saturating_add(1);
                self.lifecycle = ConnectionLifecycle::Ready;
                self.last_error = None;
                Ok(())
            }
            Ok(Err(error)) => {
                let error = IntegrationError::Transport(error.to_string());
                self.lifecycle = ConnectionLifecycle::Degraded;
                self.last_error = Some(error.to_string());
                Err(error)
            }
        }
    }

    pub(crate) fn disconnect(&mut self) {
        self.lifecycle = ConnectionLifecycle::Stopping;
        self.subscription.take();
        self.lifecycle = ConnectionLifecycle::Stopped;
    }

    pub(crate) fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready && self.subscription.is_some(),
            authenticated: self.lifecycle == ConnectionLifecycle::Ready
                && self.subscription.is_some(),
            last_error: self.last_error.clone(),
        }
    }

    pub(crate) async fn next_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        self.connect().await?;
        loop {
            let update = {
                let subscription = self
                    .subscription
                    .as_mut()
                    .ok_or(IntegrationError::NotReady)?;
                tokio::select! {
                    changed = self.notices.changed() => {
                        if changed.is_err() {
                            return Err(IntegrationError::ResyncRequired(
                                "IBKR global notice stream ended".into(),
                            ));
                        }
                        let notice = self.notices.borrow_and_update().clone();
                        if let Some(notice) = notice.as_ref() {
                            if let Some(error) = notice_error(notice) {
                                return Err(error);
                            }
                        }
                        continue;
                    }
                    update = subscription.next() => update
                        .ok_or_else(|| IntegrationError::ResyncRequired(
                            "IBKR order stream ended".into(),
                        ))?
                        .map_err(transport)?,
                }
            };
            let ibapi::subscriptions::SubscriptionItem::Data(update) = update else {
                if let ibapi::subscriptions::SubscriptionItem::Notice(notice) = update {
                    observe_notice(&notice, "order-events");
                    if let Some(error) = notice_error(&notice) {
                        return Err(error);
                    }
                }
                continue;
            };
            if let OrderUpdate::OrderStatus(status) = &update {
                if !self.order_symbols.contains_key(&status.order_id) {
                    if let Some((symbol, account)) =
                        self.session.order_metadata(status.order_id).await
                    {
                        self.order_symbols.insert(status.order_id, symbol);
                        self.execution_accounts.insert(status.order_id, account);
                    }
                }
            }
            if let Some(payload) = self.normalize_update(update)? {
                let observed = payload.occurred_at_unix_nanos;
                let provider_event_id = ibkr_event_id(&payload);
                return Ok(ExternalEventEnvelope {
                    participant: self.descriptor.participant.clone(),
                    binding_id: self.descriptor.binding_id.clone(),
                    channel_id: "ibkr.order-updates".into(),
                    channel_epoch: self.channel_epoch,
                    provider_event_id: Some(provider_event_id),
                    provider_sequence: None,
                    observed_at_unix_nanos: observed,
                    received_at_unix_nanos: UnixNanos::from(now_nanos()),
                    payload,
                });
            }
        }
    }

    fn normalize_update(
        &mut self,
        update: OrderUpdate,
    ) -> Result<Option<ExternalExecutionEvent>, IntegrationError> {
        let event = match update {
            OrderUpdate::OrderStatus(status) => {
                if self
                    .execution_accounts
                    .get(&status.order_id)
                    .is_some_and(|account| {
                        !self.account_id.is_empty() && account != &self.account_id
                    })
                {
                    return Ok(None);
                }
                execution_event(
                    status.order_id,
                    self.order_symbols
                        .get(&status.order_id)
                        .map(String::as_str)
                        .unwrap_or("UNKNOWN"),
                    normalize_ibkr_order_status(
                        status.status,
                        Some(status.filled),
                        Some(status.remaining),
                    ),
                    None,
                    None,
                    None,
                    Some(decimal_f64_value(status.filled)),
                    Some(decimal_f64_value(status.remaining)),
                )?
            }
            OrderUpdate::OpenOrder(data) => {
                if !matches_filter(&data, &self.account_id, self.symbol.as_deref()) {
                    return Ok(None);
                }
                let symbol = data.contract.symbol.to_string();
                self.order_symbols.insert(data.order_id, symbol.clone());
                self.execution_accounts
                    .insert(data.order_id, data.order.account.clone());
                execution_event(
                    data.order_id,
                    &symbol,
                    normalize_ibkr_order_status(data.order_state.status, None, None),
                    Some(order_side(&data.order.action)),
                    Some(order_type(&data.order.order_type)),
                    Some(decimal_f64_value(data.order.total_quantity)),
                    None,
                    None,
                )?
            }
            OrderUpdate::ExecutionData(data) => {
                let execution = data.execution;
                let symbol = data.contract.symbol.to_string();
                if self.symbol.as_deref().is_some_and(|value| value != symbol)
                    || (!self.account_id.is_empty()
                        && !execution.account_number.is_empty()
                        && execution.account_number != self.account_id)
                {
                    return Ok(None);
                }
                self.order_symbols
                    .insert(execution.order_id, symbol.clone());
                self.execution_orders
                    .insert(execution.execution_id.clone(), execution.order_id);
                self.execution_accounts
                    .insert(execution.order_id, execution.account_number.clone());
                ExternalExecutionEvent {
                    order_id: typed_order_id(execution.order_id)?,
                    symbol: typed_symbol(&symbol)?,
                    status: kairos_primitives::OrderStatus::Filled,
                    side: Some(
                        if format!("{:?}", execution.side).eq_ignore_ascii_case("sold") {
                            OrderSide::Sell
                        } else {
                            OrderSide::Buy
                        },
                    ),
                    order_type: None,
                    quantity: Some(decimal_f64_value(execution.shares)),
                    limit_price: None,
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: Some(decimal_f64_value(execution.shares)),
                    fill_price: Some(decimal_f64_value(execution.price)),
                    execution_id: Some(typed_fill_id(&execution.execution_id)?),
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
                            .map(String::as_str)
                            .unwrap_or("UNKNOWN"),
                    )?,
                    status: kairos_primitives::OrderStatus::Unknown,
                    side: None,
                    order_type: None,
                    quantity: None,
                    limit_price: None,
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: None,
                    fill_price: None,
                    execution_id: Some(typed_fill_id(&report.execution_id)?),
                    fee_currency: Some(Currency::new(report.currency).map_err(invalid_payload)?),
                    fee_amount: Some(decimal_f64_value(report.commission)),
                    occurred_at_unix_nanos: now_nanos().into(),
                    reason: String::new(),
                }
            }
        };
        Ok(Some(event))
    }
}

pub(super) fn notice_error(notice: &ibapi::Notice) -> Option<IntegrationError> {
    match notice.code {
        1100 => Some(IntegrationError::ResyncRequired(format!(
            "IBKR connectivity lost: {}",
            notice.message
        ))),
        1101 => Some(IntegrationError::ResyncRequired(format!(
            "IBKR connectivity restored with data loss: {}",
            notice.message
        ))),
        1300 => Some(IntegrationError::ResyncRequired(format!(
            "IBKR socket port reset: {}",
            notice.message
        ))),
        _ => None,
    }
}

fn observe_notice(notice: &ibapi::Notice, channel: &str) {
    if notice_error(notice).is_some() {
        tracing::warn!(
            event = "ibkr_notice_requires_resync",
            component = "integration",
            channel,
            code = notice.code,
            message = %notice.message,
            "IBKR notice requires reconciliation"
        );
    } else {
        tracing::info!(
            event = "ibkr_notice",
            component = "integration",
            channel,
            code = notice.code,
            message = %notice.message,
            "IBKR notice observed"
        );
    }
}

pub(super) async fn collect_orders(
    subscription: Subscription<Orders>,
) -> Result<Vec<OrderData>, IntegrationError> {
    let mut stream = subscription.filter_data();
    let mut rows = Vec::new();
    while let Some(item) = stream.next().await {
        if let Orders::OrderData(order) = item.map_err(transport)? {
            rows.push(order);
        }
    }
    Ok(rows)
}

fn normalize_orders(
    rows: Vec<OrderData>,
    binding_id: &str,
    account_id: &str,
    query: &ExternalOrderQuery,
) -> Result<Vec<ExternalOrder>, IntegrationError> {
    let mut orders = rows
        .into_iter()
        .filter(|row| matches_filter(row, account_id, query.symbol.as_ref().map(Symbol::as_str)))
        .map(|row| normalize_order(row, binding_id))
        .collect::<Result<Vec<_>, _>>()?;
    if let Some(order_id) = &query.order_id {
        orders.retain(|order| &order.order_id == order_id);
    }
    if let Some(limit) = query.limit {
        orders.truncate(limit as usize);
    }
    Ok(orders)
}

fn normalize_order(data: OrderData, binding_id: &str) -> Result<ExternalOrder, IntegrationError> {
    let order_ref = data.order.order_ref.trim();
    let status = normalize_ibkr_order_status(data.order_state.status, None, None);
    let quantity = decimal_f64_value(data.order.total_quantity);
    Ok(ExternalOrder {
        binding_id: binding_id.into(),
        order_id: typed_order_id(data.order_id)?,
        client_order_id: (!order_ref.is_empty())
            .then(|| ClientOrderId::new(order_ref))
            .transpose()
            .map_err(invalid_payload)?,
        symbol: typed_symbol(&data.contract.symbol.to_string())?,
        side: order_side(&data.order.action),
        order_type: order_type(&data.order.order_type),
        status,
        quantity,
        filled_quantity: if status == kairos_primitives::OrderStatus::Filled {
            quantity
        } else {
            DecimalValue::default()
        },
        average_fill_price: None,
        occurred_at_unix_millis: None,
    })
}

fn matches_filter(data: &OrderData, account_id: &str, symbol: Option<&str>) -> bool {
    (account_id.is_empty() || data.order.account.is_empty() || data.order.account == account_id)
        && symbol.is_none_or(|value| value == data.contract.symbol.to_string())
}

fn execution_event(
    order_id: i32,
    symbol: &str,
    status: kairos_primitives::OrderStatus,
    side: Option<OrderSide>,
    order_type: Option<OrderType>,
    quantity: Option<DecimalValue>,
    filled_quantity: Option<DecimalValue>,
    remaining_quantity: Option<DecimalValue>,
) -> Result<ExternalExecutionEvent, IntegrationError> {
    Ok(ExternalExecutionEvent {
        order_id: typed_order_id(order_id)?,
        symbol: typed_symbol(symbol)?,
        status,
        side,
        order_type,
        quantity,
        limit_price: None,
        filled_quantity,
        remaining_quantity,
        fill_quantity: None,
        fill_price: None,
        execution_id: None,
        fee_currency: None,
        fee_amount: None,
        occurred_at_unix_nanos: now_nanos().into(),
        reason: String::new(),
    })
}

fn order_side(action: &ibapi::orders::Action) -> OrderSide {
    if format!("{action:?}").eq_ignore_ascii_case("buy") {
        OrderSide::Buy
    } else {
        OrderSide::Sell
    }
}

fn order_type(value: &str) -> OrderType {
    match value.trim().to_ascii_uppercase().as_str() {
        "MKT" => OrderType::Market,
        "STP" => OrderType::Stop,
        "STP LMT" | "STP_LMT" => OrderType::StopLimit,
        _ => OrderType::Limit,
    }
}

fn typed_order_id(value: i32) -> Result<OrderId, IntegrationError> {
    OrderId::new(format!("ibkr:{value}")).map_err(invalid_payload)
}

fn parse_remote_order_id(value: &str) -> Result<i32, IntegrationError> {
    value
        .strip_prefix("ibkr:")
        .unwrap_or(value)
        .parse::<i32>()
        .map_err(|_| {
            IntegrationError::InvalidRequest("IBKR remote order id must be numeric".into())
        })
}

fn ibkr_event_id(event: &ExternalExecutionEvent) -> String {
    fn decimal(value: Option<DecimalValue>) -> String {
        value
            .map(|value| format!("{}e-{}", value.mantissa, value.scale))
            .unwrap_or_else(|| "-".into())
    }
    format!(
        "ibkr:{}:{:?}:{}:{}:{}:{}:{}:{}",
        event.order_id,
        event.status,
        decimal(event.filled_quantity),
        decimal(event.remaining_quantity),
        decimal(event.fill_quantity),
        decimal(event.fill_price),
        event
            .execution_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_else(|| "-".into()),
        decimal(event.fee_amount),
    )
}

fn typed_symbol(value: &str) -> Result<Symbol, IntegrationError> {
    Symbol::new(value).map_err(invalid_payload)
}

fn typed_fill_id(value: &str) -> Result<FillId, IntegrationError> {
    FillId::new(format!("ibkr:{value}")).map_err(invalid_payload)
}

fn decimal_f64(value: DecimalValue) -> f64 {
    value.mantissa as f64 / 10_f64.powi(value.scale as i32)
}

fn decimal_f64_value(value: f64) -> DecimalValue {
    DecimalValue::new((value * 100_000_000.0).round() as i64, 8)
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

fn transport(error: impl ToString) -> IntegrationError {
    IntegrationError::Transport(error.to_string())
}

fn invalid_payload(error: impl ToString) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn order_normalization_preserves_binding_and_canonical_remote_identity() {
        let mut data = OrderData {
            order_id: 42,
            contract: Contract::stock("AAPL").build(),
            ..OrderData::default()
        };
        data.order.action = ibapi::orders::Action::Buy;
        data.order.total_quantity = 2.5;
        data.order.order_type = "LMT".into();
        data.order.order_ref = "local-order-7".into();

        let order = normalize_order(data, "ibkr.principal.test").unwrap();

        assert_eq!(order.binding_id, "ibkr.principal.test");
        assert_eq!(order.order_id.as_str(), "ibkr:42");
        assert_eq!(
            order.client_order_id.as_ref().map(ClientOrderId::as_str),
            Some("local-order-7")
        );
        assert_eq!(order.symbol.as_str(), "AAPL");
        assert_eq!(order.side, OrderSide::Buy);
        assert_eq!(order.order_type, OrderType::Limit);
        assert_eq!(order.quantity, DecimalValue::new(250_000_000, 8));
    }

    #[test]
    fn cancel_accepts_only_the_ibkr_numeric_identity_shape() {
        assert_eq!(parse_remote_order_id("ibkr:42").unwrap(), 42);
        assert_eq!(parse_remote_order_id("42").unwrap(), 42);
        assert!(parse_remote_order_id("ibkr:not-a-number").is_err());
    }

    #[test]
    fn ibkr_statuses_preserve_partial_and_transitional_semantics() {
        use ibapi::orders::OrderStatusKind;

        assert_eq!(
            normalize_ibkr_order_status(OrderStatusKind::Submitted, Some(1.0), Some(2.0)),
            kairos_primitives::OrderStatus::PartiallyFilled
        );
        assert_eq!(
            normalize_ibkr_order_status(OrderStatusKind::PreSubmitted, None, None),
            kairos_primitives::OrderStatus::Acknowledged
        );
        assert_eq!(
            normalize_ibkr_order_status(OrderStatusKind::PendingCancel, None, None),
            kairos_primitives::OrderStatus::Accepted
        );
        assert_eq!(
            normalize_ibkr_order_status(OrderStatusKind::Cancelled, None, None),
            kairos_primitives::OrderStatus::Canceled
        );
    }

    #[test]
    fn ibkr_order_types_preserve_stop_variants() {
        assert_eq!(order_type("MKT"), OrderType::Market);
        assert_eq!(order_type("LMT"), OrderType::Limit);
        assert_eq!(order_type("STP"), OrderType::Stop);
        assert_eq!(order_type("STP LMT"), OrderType::StopLimit);
    }

    #[test]
    fn connectivity_notices_require_route_reconciliation() {
        let notice = |code, message: &str| ibapi::Notice {
            code,
            message: message.into(),
            error_time: None,
            advanced_order_reject_json: String::new(),
        };

        for code in [1100, 1101, 1300] {
            assert!(matches!(
                notice_error(&notice(code, "connectivity transition")),
                Some(IntegrationError::ResyncRequired(_))
            ));
        }
        assert!(notice_error(&notice(1102, "connectivity maintained")).is_none());
        assert!(notice_error(&notice(2104, "market data farm is OK")).is_none());
    }

    #[test]
    fn event_identity_distinguishes_execution_and_commission_for_the_same_execution_id() {
        let mut execution = execution_event(
            42,
            "AAPL",
            kairos_primitives::OrderStatus::Filled,
            Some(OrderSide::Buy),
            Some(OrderType::Market),
            Some(DecimalValue::new(1, 0)),
            Some(DecimalValue::new(1, 0)),
            Some(DecimalValue::default()),
        )
        .unwrap();
        execution.execution_id = Some(typed_fill_id("exec-1").unwrap());
        execution.fill_quantity = Some(DecimalValue::new(1, 0));
        let mut commission = execution.clone();
        commission.fill_quantity = None;
        commission.fee_amount = Some(DecimalValue::new(125, 2));

        assert_ne!(ibkr_event_id(&execution), ibkr_event_id(&commission));
        assert_eq!(ibkr_event_id(&execution), ibkr_event_id(&execution));
    }

    #[tokio::test]
    async fn shared_session_metadata_resolves_status_before_open_order_callback() {
        let session = IbkrAsyncSession::new(
            IbkrOptions::new("127.0.0.1", 4002, 7).unwrap(),
            "DU123".into(),
        );
        session
            .remember_order(42, "AAPL".into(), "DU123".into())
            .await;

        assert_eq!(
            session.order_metadata(42).await,
            Some(("AAPL".into(), "DU123".into()))
        );
    }
}
