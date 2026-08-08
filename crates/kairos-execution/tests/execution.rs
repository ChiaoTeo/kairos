use kairos_execution::application::ExecutionPreflight;
use kairos_execution::application::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestRequest, CancelOrder,
    ExecuteStrategyIntent, ExecutionAuditQuery, ExecutionFillReport, RemoteExecutionEvent,
    SubmitOrder,
};
use kairos_execution::composition::{
    compose_order_entry, ExecutionConnectionOptions, FileExecutionStore,
};
use kairos_execution::ExecutionProcess;
use kairos_execution::{
    ExecutionApplication, ExecutionEvent, ExecutionOrderStatus, OrderSide, OrderType,
    SqliteExecutionAudit,
};
use kairos_integration::application::{
    Connection, ExecutionStreamConnection, ExternalExecutionEvent, OrderEntryConnection,
};
use kairos_integration::domain::{
    AccessScope, ConnectionHealth, ConnectionIdentity, ConnectionLifecycle, ConnectionSpec,
    ConnectionState, IntegrationCapability, OrderEntryEvent, OrderEntryRequest, ProductFamily,
    TransportKind,
};
use kairos_workspace::control::RestControlClient;

struct FailingOrderEntry {
    state: ConnectionState,
}

impl FailingOrderEntry {
    fn new() -> Self {
        let spec = ConnectionSpec {
            connection_id: "execution.fixture".into(),
            route: kairos_integration::IntegrationRoute::exchange("fixture"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::OrderEntry,
            credential_id: None,
            asset_type: None,
        };
        let identity = ConnectionIdentity::new(
            spec.connection_id,
            spec.route,
            spec.product,
            spec.access,
            spec.transport,
            spec.capability,
        )
        .unwrap();
        Self {
            state: ConnectionState::new(identity),
        }
    }
}

impl Connection for FailingOrderEntry {
    fn identity(&self) -> &ConnectionIdentity {
        &self.state.identity
    }
    fn state(&self) -> &ConnectionState {
        &self.state
    }
    fn start(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }
    fn stop(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.start()
    }
    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: true,
            authenticated: true,
            last_error: None,
        }
    }
}

impl OrderEntryConnection for FailingOrderEntry {
    fn submit_order(&mut self, _request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        Err("fixture submission failed".into())
    }
    fn cancel_order(
        &mut self,
        _request: &OrderEntryRequest,
        _venue_order_id: &str,
        _at_unix_nanos: u64,
    ) -> Result<OrderEntryEvent, String> {
        Err("fixture cancellation failed".into())
    }
}

fn application(path: &std::path::Path) -> ExecutionApplication {
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        provider: "simulated".into(),
        product: "spot".into(),
        api_key: String::new(),
        secret: String::new(),
        passphrase: String::new(),
        base_url: "https://api.binance.com".into(),
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

struct TestPreflight;

impl ExecutionPreflight for TestPreflight {
    fn plan_intent(&mut self, intent: &ExecuteStrategyIntent) -> Result<Vec<SubmitOrder>, String> {
        Ok(intent
            .account_ids
            .iter()
            .enumerate()
            .map(|(index, account_id)| SubmitOrder {
                order_id: format!("{}:order:{}", intent.intent_id, index),
                intent_id: Some(intent.intent_id.clone()),
                account_id: account_id.clone(),
                segment_key: intent.segment_key.clone(),
                instrument_id: intent.instrument_id.clone(),
                market_id: intent.market_id.clone(),
                side: OrderSide::Buy,
                order_type: if intent.limit_price_mantissa.is_some() {
                    OrderType::Limit
                } else {
                    OrderType::Market
                },
                quantity_mantissa: intent.target_quantity_mantissa,
                quantity_scale: intent.quantity_scale,
                limit_price_mantissa: intent.limit_price_mantissa,
                limit_price_scale: intent.limit_price_scale,
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
    fn new(event: RemoteExecutionEvent) -> Self {
        let spec = ConnectionSpec {
            connection_id: "execution.fixture.stream".into(),
            route: kairos_integration::IntegrationRoute::exchange("fixture"),
            product: Some(ProductFamily::Equity),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::ExecutionStream,
            credential_id: None,
            asset_type: None,
        };
        let identity = ConnectionIdentity::new(
            spec.connection_id,
            spec.route,
            spec.product,
            spec.access,
            spec.transport,
            spec.capability,
        )
        .unwrap();
        Self {
            event: Some(ExternalExecutionEvent {
                order_id: event.order_id,
                symbol: event.symbol,
                status: event.status,
                side: None,
                order_type: None,
                quantity: None,
                limit_price: None,
                filled_quantity: None,
                remaining_quantity: None,
                fill_quantity: event.fill_quantity.map(|value| decimal(&value)),
                fill_price: event.fill_price.map(|value| decimal(&value)),
                execution_id: event.execution_id,
                fee_currency: event.fee_currency,
                fee_amount: event.fee_amount.map(|value| decimal(&value)),
                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                reason: event.reason,
            }),
            state: ConnectionState::new(identity),
        }
    }
}

impl Connection for OneExecutionEvent {
    fn identity(&self) -> &ConnectionIdentity {
        &self.state.identity
    }
    fn state(&self) -> &ConnectionState {
        &self.state
    }
    fn start(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }
    fn stop(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.start()
    }
    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: true,
            authenticated: true,
            last_error: None,
        }
    }
}

impl ExecutionStreamConnection for OneExecutionEvent {
    fn next_execution_event(&mut self) -> Result<Option<ExternalExecutionEvent>, String> {
        Ok(self.event.take())
    }
}

fn decimal(value: &str) -> kairos_integration::domain::DecimalValue {
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    kairos_integration::domain::DecimalValue {
        mantissa: format!("{whole}{fraction}").parse().unwrap(),
        scale: fraction.len() as u8,
    }
}

#[test]
fn execution_stream_consumption_reconciles_a_remote_fill() {
    let directory = tempfile::tempdir().unwrap();
    let state = directory.path().join("execution.json");
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        provider: "simulated".into(),
        product: "spot".into(),
        api_key: String::new(),
        secret: String::new(),
        passphrase: String::new(),
        base_url: "https://api.binance.com".into(),
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    })
    .unwrap();
    let mut app = ExecutionApplication::with_dependencies_and_query_and_stream(
        "execution",
        Some(connection),
        None,
        Some(Box::new(OneExecutionEvent::new(RemoteExecutionEvent {
            order_id: "local-1".into(),
            symbol: "BTCUSDT".into(),
            status: "Filled".into(),
            fill_quantity: Some("1".into()),
            fill_price: Some("100".into()),
            execution_id: Some("exec-1".into()),
            fee_currency: None,
            fee_amount: None,
            occurred_at_unix_nanos: 42,
            reason: String::new(),
        }))),
        Some(Box::new(FileExecutionStore::new(&state))),
    )
    .unwrap();
    app.submit(SubmitOrder {
        order_id: "local-1".into(),
        intent_id: None,
        account_id: "main".into(),
        segment_key: "spot".into(),
        instrument_id: "BTCUSDT".into(),
        market_id: None,
        side: OrderSide::Buy,
        order_type: OrderType::Market,
        quantity_mantissa: 1,
        quantity_scale: 0,
        limit_price_mantissa: None,
        limit_price_scale: None,
        options: Default::default(),
    })
    .unwrap();
    let (_, order) = app.consume_remote_execution_event().unwrap().unwrap();
    assert_eq!(order.status, ExecutionOrderStatus::Filled);
    assert_eq!(app.snapshot().fills.len(), 1);
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
    let submit = serde_json::to_vec(&SubmitOrder {
        order_id: "server-order".into(),
        intent_id: None,
        account_id: "main".into(),
        segment_key: "spot".into(),
        instrument_id: "BTCUSDT".into(),
        market_id: None,
        side: OrderSide::Buy,
        order_type: OrderType::Market,
        quantity_mantissa: 1,
        quantity_scale: 0,
        limit_price_mantissa: None,
        limit_price_scale: None,
        options: Default::default(),
    })
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

#[test]
fn one_shot_execution_application_does_not_need_server() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    let order = first
        .submit(SubmitOrder {
            order_id: "order-1".into(),
            intent_id: Some("intent-1".into()),
            account_id: "main".into(),
            segment_key: "spot".into(),
            instrument_id: "BTCUSDT".into(),
            market_id: None,
            side: OrderSide::Buy,
            order_type: OrderType::Market,
            quantity_mantissa: 1,
            quantity_scale: 0,
            limit_price_mantissa: None,
            limit_price_scale: None,
            options: Default::default(),
        })
        .unwrap();
    assert_eq!(
        order.status,
        kairos_execution::ExecutionOrderStatus::Accepted
    );

    let mut second = application(&path);
    assert_eq!(second.orders(Some("main")).len(), 1);
    let canceled = second
        .cancel(CancelOrder {
            order_id: "order-1".into(),
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
fn failed_submission_is_persisted_as_unknown_for_reconciliation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::with_dependencies(
        "execution",
        Some(Box::new(FailingOrderEntry::new())),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    let result = app.submit(SubmitOrder {
        order_id: "unknown-1".into(),
        intent_id: None,
        account_id: "main".into(),
        segment_key: "spot".into(),
        instrument_id: "BTCUSDT".into(),
        market_id: None,
        side: OrderSide::Buy,
        order_type: OrderType::Market,
        quantity_mantissa: 1,
        quantity_scale: 0,
        limit_price_mantissa: None,
        limit_price_scale: None,
        options: Default::default(),
    });
    assert!(result.is_err());
    assert_eq!(
        app.orders(Some("main"))[0].status,
        ExecutionOrderStatus::Unknown
    );
    assert_eq!(app.trace("unknown-1").len(), 2);
}

#[test]
fn strategy_intent_is_execution_owned_and_restored_with_events() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    let accepted = first
        .submit_intent(ExecuteStrategyIntent {
            intent_id: "strategy:intent:1".into(),
            strategy_id: "strategy".into(),
            launch_id: "launch".into(),
            instance_id: "instance".into(),
            instrument_id: "BTCUSDT".into(),
            market_id: Some("market:btc".into()),
            account_ids: vec!["main".into(), "hedge".into()],
            segment_key: "spot".into(),
            target_quantity_mantissa: 100,
            quantity_scale: 0,
            limit_price_mantissa: Some(10_000),
            limit_price_scale: Some(0),
            source_snapshot_id: Some("market:7".into()),
            source_event_sequence: Some(42),
            reason: "rebalance".into(),
        })
        .unwrap();
    assert_eq!(accepted.status, kairos_execution::IntentStatus::Executing);
    assert_eq!(first.intents().len(), 1);
    assert_eq!(first.intent_events(None).len(), 3);

    let second = application(&path);
    assert_eq!(
        second.intents()[0].intent.account_ids,
        vec!["main", "hedge"]
    );
    assert_eq!(second.intent_events(Some("strategy:intent:1")).len(), 3);
}

#[test]
fn intent_reaches_satisfied_after_all_child_orders_fill() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app
        .submit_intent(ExecuteStrategyIntent {
            intent_id: "intent:satisfied".into(),
            strategy_id: "strategy".into(),
            launch_id: "launch".into(),
            instance_id: "instance".into(),
            instrument_id: "BTCUSDT".into(),
            market_id: None,
            account_ids: vec!["main".into(), "hedge".into()],
            segment_key: "spot".into(),
            target_quantity_mantissa: 3,
            quantity_scale: 0,
            limit_price_mantissa: None,
            limit_price_scale: None,
            source_snapshot_id: None,
            source_event_sequence: None,
            reason: String::new(),
        })
        .unwrap();
    assert_eq!(state.status, kairos_execution::IntentStatus::Executing);
    for (index, order_id) in state.order_ids.iter().enumerate() {
        app.record_fill(ExecutionFillReport {
            fill_id: format!("fill:{index}"),
            order_id: order_id.clone(),
            quantity_mantissa: 3,
            quantity_scale: 0,
            price_mantissa: 100,
            price_scale: 0,
            fee_mantissa: 0,
            fee_scale: 0,
            occurred_at_unix_nanos: None,
        })
        .unwrap();
    }
    assert_eq!(
        app.intents()[0].status,
        kairos_execution::IntentStatus::Satisfied
    );
}

#[test]
fn already_satisfied_intent_is_terminal_without_child_orders() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        provider: "simulated".into(),
        product: "spot".into(),
        api_key: String::new(),
        secret: String::new(),
        passphrase: String::new(),
        base_url: "https://api.binance.com".into(),
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
        .submit_intent(ExecuteStrategyIntent {
            intent_id: "intent:already-satisfied".into(),
            strategy_id: "strategy".into(),
            launch_id: "launch".into(),
            instance_id: "instance".into(),
            instrument_id: "BTCUSDT".into(),
            market_id: None,
            account_ids: vec!["main".into()],
            segment_key: "spot".into(),
            target_quantity_mantissa: 0,
            quantity_scale: 0,
            limit_price_mantissa: None,
            limit_price_scale: None,
            source_snapshot_id: None,
            source_event_sequence: None,
            reason: String::new(),
        })
        .unwrap();
    assert_eq!(state.status, kairos_execution::IntentStatus::Satisfied);
    assert!(state.order_ids.is_empty());
}

#[test]
fn intent_idempotency_survives_restart_without_creating_a_second_intent() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let intent = ExecuteStrategyIntent {
        intent_id: "intent:idempotent".into(),
        strategy_id: "strategy".into(),
        launch_id: "launch".into(),
        instance_id: "instance".into(),
        instrument_id: "BTCUSDT".into(),
        market_id: None,
        account_ids: vec!["main".into()],
        segment_key: "spot".into(),
        target_quantity_mantissa: 1,
        quantity_scale: 0,
        limit_price_mantissa: None,
        limit_price_scale: None,
        source_snapshot_id: None,
        source_event_sequence: None,
        reason: String::new(),
    };
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
    let request = SubmitOrder {
        order_id: "live-order".into(),
        intent_id: None,
        account_id: "main".into(),
        segment_key: "spot".into(),
        instrument_id: "BTCUSDT".into(),
        market_id: None,
        side: OrderSide::Buy,
        order_type: OrderType::Market,
        quantity_mantissa: 1,
        quantity_scale: 0,
        limit_price_mantissa: None,
        limit_price_scale: None,
        options: Default::default(),
    };
    assert!(app.submit(request.clone()).is_err());
    let preview = app.preview_submit(&request).unwrap();
    assert_eq!(preview.reason, "dry-run preview");
    assert!(app.orders(None).is_empty());
}

#[test]
fn backtest_metrics_reproduce_closed_trade_and_drawdown_facts() {
    let metrics = BacktestApplication::evaluate(BacktestRequest {
        initial_equity: "100".into(),
        equity_curve: vec![
            BacktestEquityPoint {
                observed_at_unix_nanos: 1,
                equity: "100".into(),
            },
            BacktestEquityPoint {
                observed_at_unix_nanos: 2,
                equity: "110".into(),
            },
            BacktestEquityPoint {
                observed_at_unix_nanos: 3,
                equity: "105".into(),
            },
        ],
        fills: vec![
            BacktestFill {
                instrument_id: "BTCUSDT".into(),
                side: OrderSide::Buy,
                quantity: "1".into(),
                price: "10".into(),
                fee: "0.1".into(),
                occurred_at_unix_nanos: 1,
            },
            BacktestFill {
                instrument_id: "BTCUSDT".into(),
                side: OrderSide::Sell,
                quantity: "1".into(),
                price: "12".into(),
                fee: "0.1".into(),
                occurred_at_unix_nanos: 2,
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
fn execution_audit_publisher_writes_immutable_event_rows() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("audit.sqlite");
    let mut audit = SqliteExecutionAudit::new(&path);
    audit
        .publish(&ExecutionEvent {
            order_id: "order-1".into(),
            status: ExecutionOrderStatus::Accepted,
            venue_order_id: Some("venue-1".into()),
            occurred_at_unix_nanos: 42,
            reason: String::new(),
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        })
        .unwrap();
    audit
        .publish(&ExecutionEvent {
            order_id: "order-1".into(),
            status: ExecutionOrderStatus::Accepted,
            venue_order_id: Some("venue-1".into()),
            occurred_at_unix_nanos: 42,
            reason: String::new(),
            fill_id: None,
            filled_quantity_mantissa: None,
            filled_quantity_scale: None,
        })
        .unwrap();
    let connection = rusqlite::Connection::open(path).unwrap();
    let count: i64 = connection
        .query_row("SELECT COUNT(*) FROM execution_events", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(count, 1);
    let events = audit
        .query(&ExecutionAuditQuery {
            order_id: Some("order-1".into()),
            ..Default::default()
        })
        .unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].venue_order_id.as_deref(), Some("venue-1"));
}

#[test]
fn fills_are_recorded_cumulatively_and_restore_with_order_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.submit(SubmitOrder {
        order_id: "order-fill".into(),
        intent_id: None,
        account_id: "main".into(),
        segment_key: "spot".into(),
        instrument_id: "BTCUSDT".into(),
        market_id: None,
        side: OrderSide::Buy,
        order_type: OrderType::Limit,
        quantity_mantissa: 10,
        quantity_scale: 0,
        limit_price_mantissa: Some(100),
        limit_price_scale: Some(0),
        options: Default::default(),
    })
    .unwrap();
    let partial = app
        .record_fill(ExecutionFillReport {
            fill_id: "fill-1".into(),
            order_id: "order-fill".into(),
            quantity_mantissa: 4,
            quantity_scale: 0,
            price_mantissa: 100,
            price_scale: 0,
            fee_mantissa: 1,
            fee_scale: 0,
            occurred_at_unix_nanos: Some(10),
        })
        .unwrap();
    assert_eq!(partial.status, ExecutionOrderStatus::PartiallyFilled);
    let filled = app
        .record_fill(ExecutionFillReport {
            fill_id: "fill-2".into(),
            order_id: "order-fill".into(),
            quantity_mantissa: 6,
            quantity_scale: 0,
            price_mantissa: 101,
            price_scale: 0,
            fee_mantissa: 1,
            fee_scale: 0,
            occurred_at_unix_nanos: Some(11),
        })
        .unwrap();
    assert_eq!(filled.status, ExecutionOrderStatus::Filled);
    assert_eq!(filled.filled_quantity_mantissa, 10);
    assert_eq!(app.fills(Some("order-fill")).len(), 2);

    let restored = application(&path);
    assert_eq!(restored.fills(None).len(), 2);
    assert_eq!(
        restored.orders(None)[0].status,
        ExecutionOrderStatus::Filled
    );
}
