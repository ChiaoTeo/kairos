use kairos_domain_types::Price;
use kairos_domain_types::{
    AccountId, ClientOrderId, Currency, FillId, InstrumentId, IntentId, LegId, MarketId, OrderId,
    Quantity, SegmentKey, Symbol, UnixNanos,
};
use kairos_execution::application::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestRequest, CancelOrder,
    ExecuteStrategyIntent, ExecutionAuditQuery, ExecutionFillReport, RefreshQuoteIntent,
    RemoteOrderUpdate, SubmitOrder,
};
use kairos_execution::application::{ExecutionPreflight, QuoteObservation};
use kairos_execution::composition::{
    compose_order_entry, ExecutionConnectionOptions, FileExecutionStore, QueuedExecutionPreflight,
    SimulationConfig, SimulationOrderRequest, SimulationOrderStatus, SqlxExecutionStore,
};
use kairos_execution::ExecutionProcess;
use kairos_execution::{
    ExecutionApplication, ExecutionError, ExecutionEvent, ExecutionOrderStatus, HedgePolicy,
    OrderSide, OrderType, SqlxExecutionAudit, UnknownRemoteOrderResolution,
};
use kairos_integration::application::{
    CommandOutcome, ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder,
    ExternalOrderQuery, IndeterminateCommand, IntegrationError,
};
use kairos_integration::application::{
    ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState, OrderEntryEvent,
    OrderEntryRequest, ParticipantKind, ParticipantRef,
};
use kairos_integration::blocking::{OrderEntryConnection, OrderEventSource, OrderQueryConnection};
use kairos_market_contract::model::{MarketObservation, Quote};
use kairos_workspace::control::RestControlClient;

fn fill_report(
    fill_id: impl Into<String>,
    order_id: impl Into<String>,
    quantity: i64,
    price: i64,
    fee: i64,
    occurred_at_unix_nanos: Option<u64>,
) -> ExecutionFillReport {
    ExecutionFillReport {
        fill_id: FillId::new(fill_id.into()).unwrap(),
        order_id: OrderId::new(order_id.into()).unwrap(),
        quantity: kairos_domain_types::Quantity::new(quantity, 0).unwrap(),
        price: Price::new(price, 0).unwrap(),
        fee: kairos_domain_types::Money::new(fee, 0),
        occurred_at_unix_nanos: occurred_at_unix_nanos.map(Into::into),
    }
}

#[allow(clippy::too_many_arguments)]
fn submit_order(
    order_id: &str,
    intent_id: Option<&str>,
    account_id: &str,
    instrument_id: &str,
    side: OrderSide,
    order_type: OrderType,
    quantity: i64,
    limit_price: Option<i64>,
    market_id: Option<&str>,
) -> SubmitOrder {
    SubmitOrder {
        order_id: OrderId::new(order_id).unwrap(),
        intent_id: intent_id.map(|value| IntentId::new(value).unwrap()),
        account_id: AccountId::new(account_id).unwrap(),
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new(instrument_id).unwrap(),
        market_id: market_id.map(|value| MarketId::new(value).unwrap()),
        side,
        order_type,
        quantity: Quantity::new(quantity, 0).unwrap(),
        limit_price: limit_price.map(|value| Price::new(value, 0).unwrap()),
        options: Default::default(),
    }
}

fn strategy_intent(
    intent_id: &str,
    quantity: i64,
    limit_price: Option<i64>,
) -> ExecuteStrategyIntent {
    ExecuteStrategyIntent {
        intent_id: IntentId::new(intent_id).unwrap(),
        strategy_id: "strategy".into(),
        launch_id: "launch".into(),
        instance_id: "instance".into(),
        instrument_id: InstrumentId::new("BTCUSDT").unwrap(),
        market_id: None,
        account_ids: vec![AccountId::new("main").unwrap()],
        segment_key: SegmentKey::new("spot").unwrap(),
        target_quantity: Quantity::new(quantity, 0).unwrap(),
        limit_price: limit_price.map(|value| Price::new(value, 0).unwrap()),
        source_snapshot_id: None,
        source_event_sequence: None,
        reason: String::new(),
        intent_type: Default::default(),
        completion_policy: Default::default(),
        failure_policy: Default::default(),
        legs: Vec::new(),
        deadline_unix_nanos: None,
        min_edge_bps: None,
        max_slippage_bps: None,
        estimated_fee_bps: None,
        hedge_policy: None,
        order_options: Default::default(),
    }
}

#[allow(clippy::too_many_arguments)]
fn intent_leg(
    leg_id: &str,
    account_id: &str,
    segment_key: &str,
    instrument_id: &str,
    market_id: Option<&str>,
    side: OrderSide,
    quantity: i64,
    limit_price: Option<i64>,
) -> kairos_execution::application::IntentLegRequest {
    kairos_execution::application::IntentLegRequest {
        leg_id: LegId::new(leg_id).unwrap(),
        account_id: AccountId::new(account_id).unwrap(),
        segment_key: SegmentKey::new(segment_key).unwrap(),
        instrument_id: InstrumentId::new(instrument_id).unwrap(),
        market_id: market_id.map(|value| MarketId::new(value).unwrap()),
        side,
        quantity: Quantity::new(quantity, 0).unwrap(),
        limit_price: limit_price.map(|value| Price::new(value, 0).unwrap()),
        target_position: false,
        options: Default::default(),
    }
}

struct FailingOrderEntry {
    submit_indeterminate: bool,
    cancel_indeterminate: bool,
}

impl FailingOrderEntry {
    fn new() -> Self {
        Self {
            submit_indeterminate: false,
            cancel_indeterminate: false,
        }
    }

    fn indeterminate() -> Self {
        let mut connection = Self::new();
        connection.submit_indeterminate = true;
        connection
    }

    fn cancel_indeterminate() -> Self {
        let mut connection = Self::new();
        connection.cancel_indeterminate = true;
        connection
    }
}

impl OrderEntryConnection for FailingOrderEntry {
    fn submit_order(
        &mut self,
        _request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        if self.submit_indeterminate {
            return Ok(CommandOutcome::Indeterminate(
                IndeterminateCommand::may_have_been_sent("fixture response was lost"),
            ));
        }
        Err(IntegrationError::Transport(
            "fixture submission failed".into(),
        ))
    }
    fn cancel_order(
        &mut self,
        _request: &OrderEntryRequest,
        _remote_order_id: &str,
        _at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        if self.cancel_indeterminate {
            return Ok(CommandOutcome::Indeterminate(
                IndeterminateCommand::may_have_been_sent("fixture cancel response was lost"),
            ));
        }
        Err(IntegrationError::Transport(
            "fixture cancellation failed".into(),
        ))
    }
}

fn application(path: &std::path::Path) -> ExecutionApplication {
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        provider: "simulated".into(),
        product: "spot".into(),
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        request_weight_per_minute: 1_000,
        cancel_reserve_weight: 50,
        order_event_queue_capacity: 1_024,
        shared_quota_ledger_path: None,
        egress_scope_id: "test-egress".into(),
        principal_scope_id: "test-principal".into(),
        orders_per_10_seconds: 50,
        orders_per_day: 160_000,
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    })
    .unwrap();
    let mut application = ExecutionApplication::with_dependencies(
        "execution",
        Some(connection),
        Some(Box::new(FileExecutionStore::new(path))),
    )
    .unwrap();
    application.attach_preflight(Box::new(TestPreflight));
    application
}

#[test]
fn queued_preflight_keeps_cross_process_commands_off_the_state_caller() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut application = ExecutionApplication::with_dependencies(
        "execution",
        Some(
            compose_order_entry(&ExecutionConnectionOptions {
                route_id: "test".into(),
                required: true,
                account_id: "main".into(),
                segment_key: "spot".into(),
                provider: "simulated".into(),
                product: "spot".into(),
                api_key: String::new().into(),
                secret: String::new().into(),
                passphrase: String::new().into(),
                base_url: "https://api.binance.com".into(),
                websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
                isolated_symbol: None,
                request_weight_per_minute: 1_000,
                cancel_reserve_weight: 50,
                order_event_queue_capacity: 1_024,
                shared_quota_ledger_path: None,
                egress_scope_id: "test-egress".into(),
                principal_scope_id: "test-principal".into(),
                orders_per_10_seconds: 50,
                orders_per_day: 160_000,
                host: "127.0.0.1".into(),
                port: 4002,
                client_id: 0,
            })
            .unwrap(),
        ),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    application.attach_preflight(Box::new(
        QueuedExecutionPreflight::start(Box::new(TestPreflight), 16).unwrap(),
    ));
    application
        .submit(submit_order(
            "queued-preflight-order",
            None,
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Market,
            1,
            None,
            None,
        ))
        .unwrap();
    assert_eq!(application.orders(Some("main")).len(), 1);
}

struct TestPreflight;

impl ExecutionPreflight for TestPreflight {
    fn latest_quote(
        &mut self,
        instrument_id: &str,
        market_id: Option<&str>,
    ) -> Result<Option<QuoteObservation>, String> {
        Ok(Some(QuoteObservation {
            instrument_id: InstrumentId::new(instrument_id).unwrap(),
            market_id: market_id.map(|value| MarketId::new(value).unwrap()),
            bid_price: Some("99".parse::<Price>().unwrap()),
            ask_price: Some("102".parse::<Price>().unwrap()),
            observed_at_unix_nanos: UnixNanos::from(0),
        }))
    }

    fn plan_intent(&mut self, intent: &ExecuteStrategyIntent) -> Result<Vec<SubmitOrder>, String> {
        if !intent.legs.is_empty() {
            return Ok(intent
                .legs
                .iter()
                .map(|leg| SubmitOrder {
                    order_id: OrderId::new(format!("{}:order:{}", intent.intent_id, leg.leg_id))
                        .unwrap(),
                    intent_id: Some(intent.intent_id.clone()),
                    account_id: leg.account_id.clone(),
                    segment_key: leg.segment_key.clone(),
                    instrument_id: leg.instrument_id.clone(),
                    market_id: leg.market_id.clone(),
                    side: leg.side,
                    order_type: if leg.limit_price.is_some() {
                        OrderType::Limit
                    } else {
                        OrderType::Market
                    },
                    quantity: leg.quantity,
                    limit_price: leg.limit_price,
                    options: leg.options.clone(),
                })
                .collect());
        }
        Ok(intent
            .account_ids
            .iter()
            .enumerate()
            .map(|(index, account_id)| SubmitOrder {
                order_id: OrderId::new(format!("{}:order:{}", intent.intent_id, index)).unwrap(),
                intent_id: Some(intent.intent_id.clone()),
                account_id: account_id.clone(),
                segment_key: intent.segment_key.clone(),
                instrument_id: intent.instrument_id.clone(),
                market_id: intent.market_id.clone(),
                side: OrderSide::Buy,
                order_type: if intent.limit_price.is_some() {
                    OrderType::Limit
                } else {
                    OrderType::Market
                },
                quantity: intent.target_quantity,
                limit_price: intent.limit_price,
                options: Default::default(),
            })
            .collect())
    }
    fn validate_order(&mut self, _: &SubmitOrder) -> Result<(), String> {
        Ok(())
    }
    fn reserve_order(&mut self, _: &SubmitOrder) -> Result<(), String> {
        Ok(())
    }
    fn release_order(&mut self, _: &str) -> Result<(), String> {
        Ok(())
    }
    fn consume_order(&mut self, _: &str) -> Result<(), String> {
        Ok(())
    }
}

struct AlreadySatisfiedPreflight;

impl ExecutionPreflight for AlreadySatisfiedPreflight {
    fn plan_intent(&mut self, _: &ExecuteStrategyIntent) -> Result<Vec<SubmitOrder>, String> {
        Ok(Vec::new())
    }
    fn validate_order(&mut self, _: &SubmitOrder) -> Result<(), String> {
        Ok(())
    }
    fn reserve_order(&mut self, _: &SubmitOrder) -> Result<(), String> {
        Ok(())
    }
    fn release_order(&mut self, _: &str) -> Result<(), String> {
        Ok(())
    }
    fn consume_order(&mut self, _: &str) -> Result<(), String> {
        Ok(())
    }
}

struct OneExecutionEvent {
    event: Option<ExternalExecutionEvent>,
    state: ConnectionState,
}

impl OneExecutionEvent {
    fn new(event: RemoteOrderUpdate) -> Self {
        let identity = ConnectionDescriptor::new(
            "execution.fixture.stream",
            ParticipantRef::new(ParticipantKind::Exchange, "fixture").unwrap(),
            "execution-stream",
        )
        .unwrap();
        Self {
            event: Some(ExternalExecutionEvent {
                order_id: event.order_id,
                symbol: event.symbol,
                status: event.status.into(),
                side: None,
                order_type: None,
                quantity: None,
                limit_price: None,
                filled_quantity: None,
                remaining_quantity: None,
                fill_quantity: event.fill_quantity.map(|value| decimal(&value.to_string())),
                fill_price: event.fill_price.map(|value| decimal(&value.to_string())),
                execution_id: event.execution_id,
                fee_currency: event.fee_currency,
                fee_amount: event.fee_amount.map(|value| decimal(&value.to_string())),
                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                reason: event.reason,
            }),
            state: ConnectionState::new(identity),
        }
    }
}

impl OrderEventSource for OneExecutionEvent {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.connect_channel()
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: true,
            last_error: None,
        }
    }

    fn try_next_order_event(
        &mut self,
    ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError> {
        Ok(self.event.take().map(|event| ExternalEventEnvelope {
            participant: self.state.identity.participant.clone(),
            binding_id: self.state.identity.binding_id.clone(),
            channel_id: "execution.fixture.stream.orders".into(),
            channel_epoch: 1,
            provider_event_id: event.execution_id.as_ref().map(ToString::to_string),
            provider_sequence: None,
            observed_at_unix_nanos: event.occurred_at_unix_nanos,
            received_at_unix_nanos: event.occurred_at_unix_nanos,
            payload: event,
        }))
    }
}

struct RecoveryOrderQuery {
    orders: Vec<ExternalOrder>,
}

impl RecoveryOrderQuery {
    fn new(orders: Vec<ExternalOrder>) -> Self {
        Self { orders }
    }
}

impl OrderQueryConnection for RecoveryOrderQuery {
    fn open_orders(
        &mut self,
        _: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        Ok(self.orders.clone())
    }
    fn order_history(
        &mut self,
        _: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        Ok(self.orders.clone())
    }
    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        Ok(self
            .orders
            .iter()
            .find(|order| query.order_id.as_deref() == Some(order.order_id.as_str()))
            .cloned())
    }
}

fn decimal(value: &str) -> kairos_integration::application::DecimalValue {
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    kairos_integration::application::DecimalValue {
        mantissa: format!("{whole}{fraction}").parse().unwrap(),
        scale: fraction.len() as u8,
    }
}

fn order_id(value: &str) -> OrderId {
    OrderId::new(value).unwrap()
}

fn symbol(value: &str) -> Symbol {
    Symbol::new(value).unwrap()
}

fn quantity(value: &str) -> kairos_domain_types::Quantity {
    value.parse().unwrap()
}

fn price(value: &str) -> kairos_domain_types::Price {
    value.parse().unwrap()
}

fn fill_id(value: &str) -> FillId {
    FillId::new(value).unwrap()
}

fn currency(value: &str) -> Currency {
    Currency::new(value).unwrap()
}

fn money(value: &str) -> kairos_domain_types::Money {
    value.parse().unwrap()
}

#[test]
fn execution_stream_consumption_reconciles_a_remote_fill() {
    let directory = tempfile::tempdir().unwrap();
    let state = directory.path().join("execution.json");
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        provider: "simulated".into(),
        product: "spot".into(),
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        request_weight_per_minute: 1_000,
        cancel_reserve_weight: 50,
        order_event_queue_capacity: 1_024,
        shared_quota_ledger_path: None,
        egress_scope_id: "test-egress".into(),
        principal_scope_id: "test-principal".into(),
        orders_per_10_seconds: 50,
        orders_per_day: 160_000,
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    })
    .unwrap();
    let mut app = ExecutionApplication::with_dependencies_and_query_and_stream(
        "execution",
        Some(connection),
        None,
        Some(Box::new(OneExecutionEvent::new(RemoteOrderUpdate {
            order_id: order_id("local-1"),
            symbol: symbol("BTCUSDT"),
            status: ExecutionOrderStatus::Filled,
            fill_quantity: Some(quantity("1")),
            fill_price: Some(price("100")),
            execution_id: Some(fill_id("exec-1")),
            fee_currency: None,
            fee_amount: None,
            occurred_at_unix_nanos: 42.into(),
            reason: String::new(),
        }))),
        Some(Box::new(FileExecutionStore::new(&state))),
    )
    .unwrap();
    app.submit(submit_order(
        "local-1",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        1,
        None,
        None,
    ))
    .unwrap();
    let (_, order) = app.consume_remote_execution_event().unwrap().unwrap();
    assert_eq!(order.status, ExecutionOrderStatus::Filled);
    assert_eq!(app.snapshot().fills.len(), 1);
}

#[test]
fn remote_query_reconciliation_persists_unknown_order_once() {
    let directory = tempfile::tempdir().unwrap();
    let state = directory.path().join("execution.json");
    let remote = ExternalOrder {
        binding_id: "execution.fixture.query".into(),
        order_id: OrderId::new("exchange-unknown-1").unwrap(),
        client_order_id: None,
        symbol: Symbol::new("BTCUSDT").unwrap(),
        side: kairos_integration::application::OrderSide::Buy,
        order_type: kairos_integration::application::OrderType::Limit,
        status: kairos_domain_types::OrderStatus::Filled,
        quantity: decimal("1"),
        filled_quantity: decimal("1"),
        average_fill_price: Some(decimal("100")),
        occurred_at_unix_millis: Some(UnixNanos::from(42_000_000)),
    };
    let mut app = ExecutionApplication::with_dependencies_and_query(
        "execution",
        None,
        Some(Box::new(RecoveryOrderQuery::new(vec![remote]))),
        Some(Box::new(FileExecutionStore::new(&state))),
    )
    .unwrap();

    assert_eq!(app.reconcile_remote_orders(Default::default()).unwrap(), 1);
    assert_eq!(app.reconcile_remote_orders(Default::default()).unwrap(), 1);
    assert_eq!(app.unknown_remote_orders().len(), 1);
    assert_eq!(
        app.unknown_remote_orders()[0].resolution,
        UnknownRemoteOrderResolution::Pending
    );
}

#[test]
fn remote_query_reconciliation_recovers_a_missed_cumulative_fill() {
    let directory = tempfile::tempdir().unwrap();
    let state = directory.path().join("execution.json");
    let remote = ExternalOrder {
        binding_id: "execution.fixture.query".into(),
        order_id: OrderId::new("exchange-recovered-fill").unwrap(),
        client_order_id: Some(ClientOrderId::new("local-recovered-fill").unwrap()),
        symbol: Symbol::new("BTCUSDT").unwrap(),
        side: kairos_integration::application::OrderSide::Buy,
        order_type: kairos_integration::application::OrderType::Limit,
        status: kairos_domain_types::OrderStatus::Filled,
        quantity: decimal("1"),
        filled_quantity: decimal("1"),
        average_fill_price: Some(decimal("100")),
        occurred_at_unix_millis: Some(UnixNanos::from(42_000_000)),
    };
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        provider: "simulated".into(),
        product: "spot".into(),
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        request_weight_per_minute: 1_000,
        cancel_reserve_weight: 50,
        order_event_queue_capacity: 1_024,
        shared_quota_ledger_path: None,
        egress_scope_id: "test-egress".into(),
        principal_scope_id: "test-principal".into(),
        orders_per_10_seconds: 50,
        orders_per_day: 160_000,
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    })
    .unwrap();
    let mut app = ExecutionApplication::with_dependencies_and_query(
        "execution",
        Some(connection),
        Some(Box::new(RecoveryOrderQuery::new(vec![remote]))),
        Some(Box::new(FileExecutionStore::new(&state))),
    )
    .unwrap();
    app.submit(submit_order(
        "local-recovered-fill",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Limit,
        1,
        Some(100),
        None,
    ))
    .unwrap();

    assert_eq!(app.reconcile_remote_orders(Default::default()).unwrap(), 2);
    let order = &app.orders(None)[0];
    assert_eq!(order.status, ExecutionOrderStatus::Filled);
    assert_eq!(order.filled_quantity.mantissa(), 1);
    assert_eq!(
        order.remote_order_id.as_deref(),
        Some("exchange-recovered-fill")
    );
    assert_eq!(app.fills(None).len(), 1);
    assert_eq!(app.reconcile_remote_orders(Default::default()).unwrap(), 0);
    assert_eq!(app.fills(None).len(), 1);
}

#[test]
fn remote_partial_fill_status_is_not_promoted_to_filled() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = application(&directory.path().join("execution.json"));
    app.submit(submit_order(
        "partial-status-order",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Limit,
        2,
        Some(100),
        None,
    ))
    .unwrap();
    let order = app
        .apply_remote_execution_event(RemoteOrderUpdate {
            order_id: order_id("partial-status-order"),
            symbol: symbol("BTCUSDT"),
            status: ExecutionOrderStatus::PartiallyFilled,
            fill_quantity: None,
            fill_price: None,
            execution_id: None,
            fee_currency: None,
            fee_amount: None,
            occurred_at_unix_nanos: 42.into(),
            reason: "remote partial status".into(),
        })
        .unwrap();
    assert_eq!(order.status, ExecutionOrderStatus::PartiallyFilled);
}

#[test]
fn unknown_remote_order_can_be_linked_to_local_order_for_recovery() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = application(&directory.path().join("execution.json"));
    app.apply_remote_execution_event(RemoteOrderUpdate {
        order_id: order_id("exchange-link-1"),
        symbol: symbol("BTCUSDT"),
        status: ExecutionOrderStatus::Accepted,
        fill_quantity: None,
        fill_price: None,
        execution_id: None,
        fee_currency: None,
        fee_amount: None,
        occurred_at_unix_nanos: 42.into(),
        reason: "observed while local state was unavailable".into(),
    })
    .unwrap_err();
    app.submit(submit_order(
        "local-link-1",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Limit,
        1,
        Some(100),
        None,
    ))
    .unwrap();
    let linked = app
        .link_unknown_remote_order("exchange-link-1", "local-link-1")
        .unwrap();
    assert_eq!(linked.remote_order_id.as_deref(), Some("exchange-link-1"));
    assert_eq!(
        app.unknown_remote_orders()[0].resolution,
        UnknownRemoteOrderResolution::LinkedToLocalOrder
    );
}

#[test]
fn unknown_remote_order_is_persisted_and_restored_for_reconciliation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::with_dependencies_and_query_and_stream(
        "execution",
        None,
        None,
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();

    let error = app
        .apply_remote_execution_event(RemoteOrderUpdate {
            order_id: order_id("remote-unknown-1"),
            symbol: symbol("BTCUSDT"),
            status: ExecutionOrderStatus::Filled,
            fill_quantity: Some(quantity("1")),
            fill_price: Some(price("100")),
            execution_id: Some(fill_id("remote-fill-1")),
            fee_currency: Some(currency("USDT")),
            fee_amount: Some(money("0.1")),
            occurred_at_unix_nanos: 42.into(),
            reason: "stream arrived before local recovery".into(),
        })
        .unwrap_err();
    assert!(error.to_string().contains("unknown order"));
    assert_eq!(app.unknown_remote_orders().len(), 1);
    assert_eq!(
        app.unknown_remote_orders()[0].resolution,
        UnknownRemoteOrderResolution::Pending
    );

    let restored = ExecutionApplication::with_dependencies_and_query_and_stream(
        "execution",
        None,
        None,
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(restored.unknown_remote_orders().len(), 1);
    assert_eq!(
        restored.unknown_remote_orders()[0].remote_order_id,
        "remote-unknown-1"
    );
}

#[tokio::test(flavor = "current_thread")]
async fn execution_server_control_round_trip_uses_same_application_path() {
    let directory = tempfile::tempdir().unwrap();
    let socket = directory.path().join("execution.sock");
    let state = directory.path().join("execution.json");
    let process = ExecutionProcess::new(application(&state), &socket);
    let task = tokio::spawn(async move { process.run().await.unwrap() });
    for _ in 0..50 {
        if socket.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    let client = RestControlClient::new(&socket);
    assert_eq!(client.health().await.unwrap()["status"], "ready");
    let submit = serde_json::to_vec(&submit_order(
        "server-order",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        1,
        None,
        None,
    ))
    .unwrap();
    let response = client
        .request_json("POST", "/v1/submit", Some(&submit))
        .await
        .unwrap();
    assert_eq!(response["order_id"], "server-order");
    let orders = client
        .request_json("GET", "/v1/orders?account_id=main", None)
        .await
        .unwrap();
    assert_eq!(orders["orders"].as_array().unwrap().len(), 1);
    client.request_json("POST", "/v1/stop", None).await.unwrap();
    task.await.unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn simulated_execution_server_fills_from_market_observation() {
    let directory = tempfile::tempdir().unwrap();
    let socket = directory.path().join("execution.sock");
    let state = directory.path().join("execution.json");
    let process = ExecutionProcess::new(application(&state), &socket).with_simulator(
        kairos_execution::composition::ExecutionSimulator::new(SimulationConfig::default())
            .unwrap(),
    );
    let task = tokio::spawn(async move { process.run().await.unwrap() });
    for _ in 0..50 {
        if socket.exists() {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    let client = RestControlClient::new(&socket);
    let submit = serde_json::to_vec(&submit_order(
        "simulated-server-order",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        2,
        None,
        Some("binance:spot"),
    ))
    .unwrap();
    client
        .request_json("POST", "/v1/submit", Some(&submit))
        .await
        .unwrap();
    let market = serde_json::to_vec(&MarketObservation::Quote(Quote {
        market_id: "binance:spot".into(),
        instrument_id: "BTCUSDT".into(),
        bid_price: Some("99".into()),
        bid_quantity: Some("10".into()),
        ask_price: Some("100".into()),
        ask_quantity: Some("2".into()),
        observed_at_unix_nanos: 42,
        source_id: "replay".into(),
    }))
    .unwrap();
    let response = client
        .request_json("POST", "/v1/backtest/market", Some(&market))
        .await
        .unwrap();
    assert_eq!(response["fills"].as_array().unwrap().len(), 1);
    let fills = client
        .request_json("GET", "/v1/fills?order_id=simulated-server-order", None)
        .await
        .unwrap();
    assert_eq!(fills["fills"].as_array().unwrap().len(), 1);
    client.request_json("POST", "/v1/stop", None).await.unwrap();
    task.await.unwrap();
}

#[test]
fn one_shot_execution_application_does_not_need_server() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    let order = first
        .submit(submit_order(
            "order-1",
            Some("intent-1"),
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Market,
            1,
            None,
            None,
        ))
        .unwrap();
    assert_eq!(
        order.status,
        kairos_execution::ExecutionOrderStatus::Accepted
    );

    let mut second = application(&path);
    assert_eq!(second.orders(Some("main")).len(), 1);
    let canceled = second
        .cancel(CancelOrder {
            order_id: OrderId::new("order-1").unwrap(),
            reason: "test".into(),
        })
        .unwrap();
    assert_eq!(
        canceled.status,
        kairos_execution::ExecutionOrderStatus::Canceled
    );
    assert_eq!(second.trace("order-1").len(), 3);
}

#[test]
fn sqlite_execution_store_reloads_the_latest_checkpoint() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution-state.sqlite");
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        provider: "simulated".into(),
        product: "spot".into(),
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        request_weight_per_minute: 1_000,
        cancel_reserve_weight: 50,
        order_event_queue_capacity: 1_024,
        shared_quota_ledger_path: None,
        egress_scope_id: "test-egress".into(),
        principal_scope_id: "test-principal".into(),
        orders_per_10_seconds: 50,
        orders_per_day: 160_000,
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    })
    .unwrap();
    let mut first = ExecutionApplication::with_dependencies(
        "execution",
        Some(connection),
        Some(Box::new(SqlxExecutionStore::new(&path).unwrap())),
    )
    .unwrap();
    first.attach_preflight(Box::new(TestPreflight));
    first
        .submit(submit_order(
            "sqlite-order",
            None,
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Market,
            1,
            None,
            None,
        ))
        .unwrap();
    let second = ExecutionApplication::with_dependencies(
        "execution",
        None,
        Some(Box::new(SqlxExecutionStore::new(&path).unwrap())),
    )
    .unwrap();
    assert_eq!(second.orders(Some("main")).len(), 1);
}

#[test]
fn sqlite_execution_store_retains_outbox_until_acknowledged() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution-state.sqlite");
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        provider: "simulated".into(),
        product: "spot".into(),
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        request_weight_per_minute: 1_000,
        cancel_reserve_weight: 50,
        order_event_queue_capacity: 1_024,
        shared_quota_ledger_path: None,
        egress_scope_id: "test-egress".into(),
        principal_scope_id: "test-principal".into(),
        orders_per_10_seconds: 50,
        orders_per_day: 160_000,
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    })
    .unwrap();
    let mut app = ExecutionApplication::with_dependencies(
        "execution",
        Some(connection),
        Some(Box::new(SqlxExecutionStore::new(&path).unwrap())),
    )
    .unwrap();
    app.attach_preflight(Box::new(TestPreflight));
    app.submit(submit_order(
        "outbox-order",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        1,
        None,
        None,
    ))
    .unwrap();

    let pending = app.pending_outbox(10).unwrap();
    assert_eq!(pending.len(), 2);
    app.acknowledge_outbox(&pending.iter().map(|entry| entry.id).collect::<Vec<_>>())
        .unwrap();
    assert!(app.pending_outbox(10).unwrap().is_empty());
}

#[test]
fn not_sent_submission_is_persisted_as_failed_without_reconciliation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::with_dependencies(
        "execution",
        Some(Box::new(FailingOrderEntry::new())),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    let result = app.submit(submit_order(
        "unknown-1",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        1,
        None,
        None,
    ));
    assert!(result.is_err());
    assert_eq!(
        app.orders(Some("main"))[0].status,
        ExecutionOrderStatus::Failed
    );
    assert_eq!(app.trace("unknown-1").len(), 2);
}

#[test]
fn indeterminate_submission_is_explicit_and_persisted_for_reconciliation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::with_dependencies(
        "execution",
        Some(Box::new(FailingOrderEntry::indeterminate())),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();

    let result = app.submit(submit_order(
        "indeterminate-1",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        1,
        None,
        None,
    ));

    assert!(matches!(result, Err(ExecutionError::Indeterminate(_))));
    assert_eq!(
        app.orders(Some("main"))[0].status,
        ExecutionOrderStatus::Unknown
    );
    assert_eq!(app.trace("indeterminate-1").len(), 2);
}

#[test]
fn not_sent_cancel_keeps_the_original_order_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.submit(submit_order(
        "cancel-not-sent",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        1,
        None,
        None,
    ))
    .unwrap();
    app.install_order_entry(Box::new(FailingOrderEntry::new()));

    let result = app.cancel(CancelOrder {
        order_id: OrderId::new("cancel-not-sent").unwrap(),
        reason: "test".into(),
    });

    assert!(matches!(result, Err(ExecutionError::Gateway(_))));
    assert_eq!(
        app.orders(Some("main"))[0].status,
        ExecutionOrderStatus::Accepted
    );
    assert_eq!(app.trace("cancel-not-sent").len(), 2);
}

#[test]
fn indeterminate_cancel_marks_the_order_unknown_for_reconciliation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.submit(submit_order(
        "cancel-indeterminate",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        1,
        None,
        None,
    ))
    .unwrap();
    app.install_order_entry(Box::new(FailingOrderEntry::cancel_indeterminate()));

    let result = app.cancel(CancelOrder {
        order_id: OrderId::new("cancel-indeterminate").unwrap(),
        reason: "test".into(),
    });

    assert!(matches!(result, Err(ExecutionError::Indeterminate(_))));
    assert_eq!(
        app.orders(Some("main"))[0].status,
        ExecutionOrderStatus::Unknown
    );
    assert_eq!(app.trace("cancel-indeterminate").len(), 3);
}

#[test]
fn strategy_intent_is_execution_owned_and_restored_with_events() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    let accepted = first
        .submit_intent({
            let mut intent = strategy_intent("strategy:intent:1", 100, Some(10_000));
            intent.market_id = Some(MarketId::new("market:btc").unwrap());
            intent.account_ids = vec![
                AccountId::new("main").unwrap(),
                AccountId::new("hedge").unwrap(),
            ];
            intent.source_snapshot_id = Some("market:7".into());
            intent.source_event_sequence = Some(42.into());
            intent.reason = "rebalance".into();
            intent
        })
        .unwrap();
    assert_eq!(accepted.status, kairos_execution::IntentStatus::Executing);
    let plan = accepted.plan.as_ref().expect("intent plan is persisted");
    assert_eq!(plan.intent_id, "strategy:intent:1");
    assert_eq!(plan.legs.len(), 2);
    assert!(plan.legs.iter().all(|leg| leg.order_ids.len() == 1));
    assert!(first.orders(None).iter().all(|order| {
        order.plan_id.as_deref() == Some("strategy:intent:1:plan:1") && order.leg_id.is_some()
    }));
    assert_eq!(first.intents().len(), 1);
    assert_eq!(first.intent_events(None).len(), 3);

    let second = application(&path);
    assert_eq!(
        second.intents()[0].intent.account_ids,
        vec![
            AccountId::new("main").unwrap(),
            AccountId::new("hedge").unwrap()
        ]
    );
    assert_eq!(second.intents()[0].plan.as_ref().unwrap().legs.len(), 2);
    assert_eq!(second.intent_events(Some("strategy:intent:1")).len(), 3);
}

#[test]
fn intent_reaches_satisfied_after_all_child_orders_fill() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app
        .submit_intent({
            let mut intent = strategy_intent("intent:satisfied", 3, None);
            intent.account_ids = vec![
                AccountId::new("main").unwrap(),
                AccountId::new("hedge").unwrap(),
            ];
            intent
        })
        .unwrap();
    assert_eq!(state.status, kairos_execution::IntentStatus::Executing);
    for (index, order_id) in state.order_ids.iter().enumerate() {
        app.record_fill(fill_report(
            format!("fill:{index}"),
            order_id.to_string(),
            3,
            100,
            0,
            None,
        ))
        .unwrap();
    }
    assert_eq!(
        app.intents()[0].status,
        kairos_execution::IntentStatus::Satisfied
    );
}

#[test]
fn pair_intent_tracks_each_leg_independently() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app
        .submit_intent({
            let mut intent = strategy_intent("intent:pair", 0, None);
            intent.reason = "cash and hedge legs".into();
            intent.intent_type = kairos_execution::IntentType::PairArbitrage;
            intent.completion_policy = kairos_execution::CompletionPolicy::AllLegsSatisfied;
            intent.failure_policy = kairos_execution::FailurePolicy::Compensate;
            intent.legs = vec![
                intent_leg(
                    "spot-buy",
                    "main",
                    "spot",
                    "BTCUSDT",
                    Some("spot:btc"),
                    OrderSide::Buy,
                    2,
                    Some(100),
                ),
                intent_leg(
                    "perp-sell",
                    "main",
                    "perp",
                    "BTC-PERP",
                    Some("perp:btc"),
                    OrderSide::Sell,
                    2,
                    Some(101),
                ),
            ];
            intent
        })
        .unwrap();
    let plan = state.plan.as_ref().unwrap();
    assert_eq!(
        plan.intent_type,
        kairos_execution::IntentType::PairArbitrage
    );
    assert_eq!(plan.legs.len(), 2);
    assert_eq!(plan.legs[0].leg_id, "spot-buy");
    assert_eq!(plan.legs[1].leg_id, "perp-sell");
    assert_ne!(plan.legs[0].order_ids, plan.legs[1].order_ids);
}

#[test]
fn two_leg_buy_sell_intent_is_satisfied_only_after_both_legs_fill() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app
        .submit_intent({
            let mut intent = strategy_intent("intent:self-funded-pair", 0, None);
            intent.strategy_id = "arb".into();
            intent.intent_type = kairos_execution::IntentType::PairArbitrage;
            intent.completion_policy = kairos_execution::CompletionPolicy::AllLegsSatisfied;
            intent.failure_policy = kairos_execution::FailurePolicy::CancelRemaining;
            intent.legs = vec![
                intent_leg(
                    "buy-leg",
                    "main",
                    "spot",
                    "BTCUSDT",
                    Some("spot"),
                    OrderSide::Buy,
                    1,
                    Some(100),
                ),
                intent_leg(
                    "sell-leg",
                    "main",
                    "perp",
                    "BTC-PERP",
                    Some("perp"),
                    OrderSide::Sell,
                    1,
                    Some(101),
                ),
            ];
            intent
        })
        .unwrap();
    let buy = state.plan.as_ref().unwrap().legs[0].order_ids[0].clone();
    let sell = state.plan.as_ref().unwrap().legs[1].order_ids[0].clone();
    app.record_fill(fill_report("buy-fill", buy.to_string(), 1, 100, 1, None))
        .unwrap();
    assert_ne!(
        app.intent("intent:self-funded-pair").unwrap().status,
        kairos_execution::IntentStatus::Satisfied
    );
    let completed = app
        .record_fill(fill_report("sell-fill", sell.to_string(), 1, 101, 1, None))
        .unwrap();
    assert_eq!(completed.status, ExecutionOrderStatus::Filled);
    assert_eq!(
        app.intent("intent:self-funded-pair").unwrap().status,
        kairos_execution::IntentStatus::Satisfied
    );
}

#[test]
fn pair_fills_create_compensation_from_actual_leader_quantity() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app
        .submit_intent({
            let mut intent = strategy_intent("intent:hedge-compensation", 0, None);
            intent.intent_type = kairos_execution::IntentType::PairArbitrage;
            intent.completion_policy = kairos_execution::CompletionPolicy::HedgeWithinTolerance;
            intent.failure_policy = kairos_execution::FailurePolicy::Compensate;
            intent.hedge_policy = Some(HedgePolicy {
                leader_leg_id: LegId::new("leader").unwrap(),
                hedge_leg_id: LegId::new("hedge").unwrap(),
                ratio: kairos_domain_types::Ratio::new(2, 1).unwrap(),
                contract_multiplier: kairos_domain_types::Ratio::new(1, 1).unwrap(),
                max_unhedged_quantity: Quantity::new(0, 0).unwrap(),
                compensate_on_failure: true,
                max_compensation_attempts: 3,
            });
            intent.legs = vec![
                intent_leg(
                    "leader",
                    "main",
                    "spot",
                    "BTCUSDT",
                    None,
                    OrderSide::Buy,
                    4,
                    Some(100),
                ),
                intent_leg(
                    "hedge",
                    "main",
                    "spot",
                    "BTCUSDT",
                    None,
                    OrderSide::Sell,
                    1,
                    Some(100),
                ),
            ];
            intent
        })
        .unwrap();
    let leader = state
        .plan
        .as_ref()
        .unwrap()
        .legs
        .iter()
        .find(|leg| leg.leg_id == "leader")
        .unwrap()
        .order_ids[0]
        .clone();
    app.record_fill(fill_report(
        "leader-fill",
        leader.to_string(),
        4,
        100,
        0,
        None,
    ))
    .unwrap();
    assert!(app
        .orders(None)
        .iter()
        .any(|order| order.order_id.contains(":compensate:8")));
    assert_eq!(
        app.hedge_requirement("intent:hedge-compensation")
            .unwrap()
            .unwrap()
            .unhedged_quantity
            .mantissa(),
        8
    );
}

#[test]
fn quote_refresh_replaces_both_legs_in_the_same_execution_plan() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app
        .submit_intent({
            let mut intent = strategy_intent("intent:quote-refresh", 0, None);
            intent.strategy_id = "maker".into();
            intent.intent_type = kairos_execution::IntentType::QuoteProvisioning;
            intent.completion_policy = kairos_execution::CompletionPolicy::BestEffort;
            intent.failure_policy = kairos_execution::FailurePolicy::ContinueOtherLegs;
            intent.legs = vec![
                {
                    let mut leg = intent_leg(
                        "bid",
                        "main",
                        "spot",
                        "BTCUSDT",
                        None,
                        OrderSide::Buy,
                        2,
                        Some(100),
                    );
                    leg.options.post_only = Some(true);
                    leg
                },
                {
                    let mut leg = intent_leg(
                        "ask",
                        "main",
                        "spot",
                        "BTCUSDT",
                        None,
                        OrderSide::Sell,
                        2,
                        Some(101),
                    );
                    leg.options.post_only = Some(true);
                    leg
                },
            ];
            intent
        })
        .unwrap();
    let refreshed = app
        .refresh_quote_intent(RefreshQuoteIntent {
            intent_id: state.intent.intent_id.clone(),
            bid_price: Price::new(99, 0).unwrap(),
            ask_price: Price::new(102, 0).unwrap(),
            quote_observed_at: 0.into(),
            reason: "new market quote".into(),
        })
        .unwrap();
    assert_eq!(refreshed.quote_version, 1);
    assert_eq!(refreshed.plan.as_ref().unwrap().legs.len(), 2);
    assert_eq!(refreshed.plan.as_ref().unwrap().legs[0].order_ids.len(), 2);
    assert_eq!(refreshed.plan.as_ref().unwrap().legs[1].order_ids.len(), 2);
    assert!(app.orders(None).iter().any(|order| {
        order.order_id.starts_with("intent:quote-refresh:quote:1:")
            && order.status == ExecutionOrderStatus::Accepted
    }));
}

#[test]
fn cancel_intent_cancels_all_active_child_orders() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app
        .submit_intent({
            let mut intent = strategy_intent("intent:cancel", 3, None);
            intent.account_ids = vec![
                AccountId::new("main").unwrap(),
                AccountId::new("hedge").unwrap(),
            ];
            intent
        })
        .unwrap();
    let canceled = app
        .cancel_intent(kairos_execution::application::CancelIntent {
            intent_id: IntentId::new("intent:cancel").unwrap(),
            reason: "strategy stopped".into(),
        })
        .unwrap();
    assert_eq!(canceled.status, kairos_execution::IntentStatus::Canceled);
    assert!(state
        .order_ids
        .iter()
        .all(|order_id| app.orders(None).iter().any(|order| {
            order.order_id == *order_id && order.status == ExecutionOrderStatus::Canceled
        })));
}

#[test]
fn expiring_intent_is_terminal_and_cancels_children() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app
        .submit_intent(strategy_intent("intent:expire", 3, None))
        .unwrap();
    let expired = app
        .expire_intent(kairos_execution::application::ExpireIntent {
            intent_id: state.intent.intent_id.clone(),
            reason: "deadline reached".into(),
        })
        .unwrap();
    assert_eq!(expired.status, kairos_execution::IntentStatus::Expired);
    assert!(state.order_ids.iter().all(|order_id| {
        app.orders(None).iter().any(|order| {
            order.order_id == *order_id && order.status == ExecutionOrderStatus::Canceled
        })
    }));
}

#[test]
fn due_intent_is_expired_by_runtime_tick() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.submit_intent({
        let mut intent = strategy_intent("intent:deadline", 1, None);
        intent.deadline_unix_nanos = Some(1.into());
        intent
    })
    .unwrap();
    assert_eq!(app.expire_due_intents(2).unwrap(), 1);
    assert_eq!(
        app.intents()[0].status,
        kairos_execution::IntentStatus::Expired
    );
}

#[test]
fn already_satisfied_intent_is_terminal_without_child_orders() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        provider: "simulated".into(),
        product: "spot".into(),
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        request_weight_per_minute: 1_000,
        cancel_reserve_weight: 50,
        order_event_queue_capacity: 1_024,
        shared_quota_ledger_path: None,
        egress_scope_id: "test-egress".into(),
        principal_scope_id: "test-principal".into(),
        orders_per_10_seconds: 50,
        orders_per_day: 160_000,
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    })
    .unwrap();
    let mut app = ExecutionApplication::with_dependencies(
        "execution",
        Some(connection),
        Some(Box::new(FileExecutionStore::new(path))),
    )
    .unwrap();
    app.attach_preflight(Box::new(AlreadySatisfiedPreflight));
    let state = app
        .submit_intent(strategy_intent("intent:already-satisfied", 0, None))
        .unwrap();
    assert_eq!(state.status, kairos_execution::IntentStatus::Satisfied);
    assert!(state.order_ids.is_empty());
}

#[test]
fn intent_idempotency_survives_restart_without_creating_a_second_intent() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let intent = strategy_intent("intent:idempotent", 1, None);
    let mut first = application(&path);
    let (_, duplicate) = first
        .submit_intent_with_idempotency(intent.clone(), "command-1".into())
        .unwrap();
    assert!(!duplicate);
    let (_, duplicate) = first
        .submit_intent_with_idempotency(intent.clone(), "command-1".into())
        .unwrap();
    assert!(duplicate);
    let second = application(&path);
    assert_eq!(second.intents().len(), 1);
    let mut second = second;
    let (_, duplicate) = second
        .submit_intent_with_idempotency(intent, "command-1".into())
        .unwrap();
    assert!(duplicate);
}

#[test]
fn live_submission_requires_confirmation_and_dry_run_does_not_commit() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.configure_live_trading(true, false);
    let request = submit_order(
        "live-order",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        1,
        None,
        None,
    );
    assert!(app.submit(request.clone()).is_err());
    let preview = app.preview_submit(&request).unwrap();
    assert_eq!(preview.reason, "dry-run preview");
    assert!(app.orders(None).is_empty());
}

#[test]
fn backtest_metrics_reproduce_closed_trade_and_drawdown_facts() {
    let metrics = BacktestApplication::evaluate(BacktestRequest {
        initial_equity: "100".parse().unwrap(),
        equity_curve: vec![
            BacktestEquityPoint {
                observed_at_unix_nanos: 1.into(),
                equity: "100".parse().unwrap(),
            },
            BacktestEquityPoint {
                observed_at_unix_nanos: 2.into(),
                equity: "110".parse().unwrap(),
            },
            BacktestEquityPoint {
                observed_at_unix_nanos: 3.into(),
                equity: "105".parse().unwrap(),
            },
        ],
        fills: vec![
            BacktestFill {
                instrument_id: InstrumentId::new("BTCUSDT").unwrap(),
                side: OrderSide::Buy,
                quantity: "1".parse().unwrap(),
                price: "10".parse().unwrap(),
                fee: "0.1".parse().unwrap(),
                occurred_at_unix_nanos: 1.into(),
            },
            BacktestFill {
                instrument_id: InstrumentId::new("BTCUSDT").unwrap(),
                side: OrderSide::Sell,
                quantity: "1".parse().unwrap(),
                price: "12".parse().unwrap(),
                fee: "0.1".parse().unwrap(),
                occurred_at_unix_nanos: 2.into(),
            },
        ],
        ..Default::default()
    })
    .unwrap();
    assert_eq!(metrics.trade_count, 1);
    assert_eq!(metrics.win_count, 1);
    assert_eq!(metrics.gross_profit, "2");
    assert_eq!(metrics.max_drawdown, "5");
}

#[test]
fn backtest_run_simulates_quote_execution_and_returns_fills() {
    let result = BacktestApplication::run(BacktestRequest {
        initial_equity: "1000".parse().unwrap(),
        orders: vec![SimulationOrderRequest {
            order_id: OrderId::new("sim-order-1").unwrap(),
            instrument_id: InstrumentId::new("BTCUSDT").unwrap(),
            side: OrderSide::Buy,
            order_type: OrderType::Market,
            quantity: "2".parse().unwrap(),
            limit_price: None,
            submitted_at_unix_nanos: 10.into(),
        }],
        market_events: vec![MarketObservation::Quote(Quote {
            market_id: "binance:spot".into(),
            instrument_id: "BTCUSDT".into(),
            bid_price: Some("99".into()),
            bid_quantity: Some("10".into()),
            ask_price: Some("100".into()),
            ask_quantity: Some("2".into()),
            observed_at_unix_nanos: 11,
            source_id: "replay".into(),
        })],
        simulation: SimulationConfig {
            fee_bps: 10.0,
            slippage_bps: 0.0,
            enforce_quote_quantity: true,
        },
        ..Default::default()
    })
    .unwrap();
    assert_eq!(result.fills.len(), 1);
    assert_eq!(result.fills[0].quantity.to_string(), "2");
    assert_eq!(result.fills[0].price.to_string(), "100");
    assert_eq!(result.fills[0].fee.to_string(), "0.2");
    assert_eq!(result.orders[0].status, SimulationOrderStatus::Filled);
}

#[test]
fn backtest_run_consumes_a_downloaded_bar_and_fills_at_close() {
    let result = BacktestApplication::run(BacktestRequest {
        initial_equity: "1000".parse().unwrap(),
        orders: vec![SimulationOrderRequest {
            order_id: OrderId::new("bar-order-1").unwrap(),
            instrument_id: InstrumentId::new("instrument:equity:US:AAPL:common").unwrap(),
            side: OrderSide::Buy,
            order_type: OrderType::Market,
            quantity: "1".parse().unwrap(),
            limit_price: None,
            submitted_at_unix_nanos: 1_699_999_999_000_000_000.into(),
        }],
        market_events: vec![MarketObservation::Bar(kairos_market_contract::model::Bar {
            market_id: "market:massive:equity:AAPL".into(),
            instrument_id: "instrument:equity:US:AAPL:common".into(),
            timeframe: "1m".into(),
            open: "100".into(),
            high: "101".into(),
            low: "99".into(),
            close: "100.5".into(),
            volume: Some("12".into()),
            observed_at_unix_nanos: 1_700_000_000_000_000_000,
            source_id: "massive".into(),
            derivation: "massive-stocks-aggregate".into(),
        })],
        ..Default::default()
    })
    .unwrap();

    assert_eq!(result.fills.len(), 1);
    assert_eq!(result.fills[0].price.to_string(), "100.5");
    assert_eq!(result.orders[0].status, SimulationOrderStatus::Filled);
}

#[test]
fn execution_audit_publisher_writes_immutable_event_rows() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("audit.sqlite");
    let mut audit = SqlxExecutionAudit::new(&path).unwrap();
    audit
        .publish(&ExecutionEvent {
            order_id: kairos_domain_types::OrderId::new("order-1").unwrap(),
            intent_id: None,
            plan_id: None,
            leg_id: None,
            status: ExecutionOrderStatus::Accepted,
            remote_order_id: Some(kairos_domain_types::RemoteOrderId::new("exchange-1").unwrap()),
            occurred_at_unix_nanos: 42.into(),
            reason: String::new(),
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        })
        .unwrap();
    audit
        .publish(&ExecutionEvent {
            order_id: kairos_domain_types::OrderId::new("order-1").unwrap(),
            intent_id: None,
            plan_id: None,
            leg_id: None,
            status: ExecutionOrderStatus::Accepted,
            remote_order_id: Some(kairos_domain_types::RemoteOrderId::new("exchange-1").unwrap()),
            occurred_at_unix_nanos: 42.into(),
            reason: String::new(),
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        })
        .unwrap();
    let events = audit
        .query(&ExecutionAuditQuery {
            order_id: Some(OrderId::new("order-1").unwrap()),
            ..Default::default()
        })
        .unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].remote_order_id.as_deref(), Some("exchange-1"));
}

#[test]
fn fills_are_recorded_cumulatively_and_restore_with_order_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.submit(submit_order(
        "order-fill",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Limit,
        10,
        Some(100),
        None,
    ))
    .unwrap();
    let partial = app
        .record_fill(fill_report("fill-1", "order-fill", 4, 100, 1, Some(10)))
        .unwrap();
    assert_eq!(partial.status, ExecutionOrderStatus::PartiallyFilled);
    let duplicate = app
        .record_fill(fill_report("fill-1", "order-fill", 4, 100, 1, Some(10)))
        .unwrap();
    assert_eq!(duplicate.status, ExecutionOrderStatus::PartiallyFilled);
    assert_eq!(app.fills(Some("order-fill")).len(), 1);
    let filled = app
        .record_fill(fill_report("fill-2", "order-fill", 6, 101, 1, Some(11)))
        .unwrap();
    assert_eq!(filled.status, ExecutionOrderStatus::Filled);
    assert_eq!(filled.filled_quantity.mantissa(), 10);
    assert_eq!(app.fills(Some("order-fill")).len(), 2);

    let restored = application(&path);
    assert_eq!(restored.fills(None).len(), 2);
    assert_eq!(
        restored.orders(None)[0].status,
        ExecutionOrderStatus::Filled
    );
}
