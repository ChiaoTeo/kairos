use super::{
    process_readiness, resync_targets, set_route_readiness, ExecutionApplication,
    ExecutionAsyncRoute, ExecutionProcess,
};
use crate::application::{ExecutionOrderOptions, SubmitOrder};
use crate::domain::{OrderSide as DomainOrderSide, OrderType};
use crate::services::control::{request_class, v2_submit_intent, RequestClass};
use crate::services::publication::ExecutionEventPublication;
use kairos_integration::application::{
    AsyncOrderEventSource, ExternalEventEnvelope, ExternalExecutionEvent, IntegrationError,
};
use kairos_integration::application::{
    ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState, OrderSide,
    ParticipantKind, ParticipantRef, ProviderInstrumentRef,
};
use kairos_integration::blocking::OrderEventSource;
use kairos_primitives::{
    AccountId, ExecutionAccessId, InstrumentId, OrderId, Quantity, SegmentKey, StrategyId,
};
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{mpsc, Arc};

#[test]
fn mutation_and_query_ingress_are_classified_separately() {
    assert!(matches!(
        request_class("POST", "/v1/intents"),
        RequestClass::Command
    ));
    assert!(matches!(
        request_class("GET", "/v1/intents"),
        RequestClass::Query
    ));
    assert!(matches!(
        request_class("GET", "/v1/orders"),
        RequestClass::Query
    ));
    assert!(matches!(
        request_class("GET", "/v1/health"),
        RequestClass::Query
    ));
    assert!(matches!(
        request_class("DELETE", "/v1/orders/order-1"),
        RequestClass::Command
    ));
}

#[test]
fn v2_intent_control_maps_a_contract_intent_into_the_application_request() {
    let body = serde_json::json!({
        "command_id": "command-1",
        "idempotency_key": "intent-1",
        "intent": {
            "intent_id": "intent-1",
            "strategy_id": "strategy-1",
            "launch_id": "launch-1",
            "instance_id": "instance-1",
            "intent_type": "SingleOrder",
            "completion_policy": "AllLegsSatisfied",
            "failure_policy": "CancelRemaining",
            "legs": [{
                "leg_id": "leg-1",
                "account_id": "account-1",
                "segment_key": "spot",
                "instrument_id": "instrument:btc",
                "market_id": "market:btc",
                "side": "buy",
                "quantity": "1.25",
                "quantity_semantics": "order_quantity",
                "options": {}
            }]
        }
    });
    let (intent, idempotency_key) = v2_submit_intent(&body.to_string()).unwrap();
    assert_eq!(idempotency_key, "intent-1");
    assert_eq!(intent.intent_id.as_str(), "intent-1");
    assert_eq!(intent.legs.len(), 1);
    assert_eq!(intent.legs[0].account_id.as_str(), "account-1");
    assert!(!intent.legs[0].target_position);
}

#[test]
fn exchange_event_identity_is_owned_by_the_actor() {
    let application = ExecutionApplication::assemble_for_test("execution", None, None)
        .expect("fixture application");
    let mut process = ExecutionProcess::new(application, PathBuf::from("/tmp/execution-test.sock"));
    assert!(process.application.accept_remote_event_identity("fill-1"));
    assert!(!process.application.accept_remote_event_identity("fill-1"));
    assert!(process.application.accept_remote_event_identity("fill-2"));
}

#[test]
fn initial_execution_snapshot_never_synthesizes_events() {
    let application = ExecutionApplication::assemble_for_test("execution", None, None)
        .expect("fixture application");
    let (capture, observed) = ExecutionEventPublication::memory();
    let mut process =
        ExecutionProcess::new(application, PathBuf::from("/tmp/execution-event-test.sock"))
            .with_event_publication(capture);

    process.publish_snapshots().unwrap();

    assert!(observed.lock().unwrap().is_empty());
}

#[test]
fn execution_commit_publishes_its_direct_business_event() {
    let mut application = ExecutionApplication::assemble_for_test("execution", None, None)
        .expect("fixture application");
    let execution_access_id = ExecutionAccessId::new("execution-access:test").unwrap();
    application.configure_execution_access(
        execution_access_id.clone(),
        ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "fixture").unwrap(),
            None,
            "BTCUSDT",
        )
        .unwrap(),
    );
    let (capture, observed) = ExecutionEventPublication::memory();
    let mut process =
        ExecutionProcess::new(application, PathBuf::from("/tmp/execution-event-test.sock"))
            .with_event_publication(capture);
    process.publish_snapshots().unwrap();

    process
        .application
        .prepare_submission(SubmitOrder {
            order_id: OrderId::new("order-1").unwrap(),
            intent_id: None,
            strategy_id: Some(StrategyId::new("strategy-1").unwrap()),
            account_id: AccountId::new("account-1").unwrap(),
            segment_key: SegmentKey::new("spot").unwrap(),
            instrument_id: InstrumentId::new("BTCUSDT").unwrap(),
            market_id: None,
            execution_access_id: Some(execution_access_id),
            side: DomainOrderSide::Buy,
            order_type: OrderType::Market,
            quantity: Quantity::new(1, 0).unwrap(),
            limit_price: None,
            options: ExecutionOrderOptions::default(),
            submitted_at_unix_nanos: Some(1.into()),
        })
        .unwrap();
    process.publish_snapshots().unwrap();

    let events = observed.lock().unwrap();
    assert!(!events.is_empty());
    assert_eq!(events[0].sequence.get(), 1);
    assert!(!events[0].changes.is_empty());
}

#[test]
fn recovery_targets_only_the_binding_owned_by_the_failed_route() {
    let application = ExecutionApplication::assemble_for_test("execution", None, None)
        .expect("fixture application");
    let process = ExecutionProcess::new(
        application,
        PathBuf::from("/tmp/execution-route-recovery-test.sock"),
    )
    .with_async_execution_routes(vec![
        ExecutionAsyncRoute::new("binance", true, ()).with_binding_id("binance.principal.main"),
        ExecutionAsyncRoute::new("okx", true, ()).with_binding_id("okx.principal.main"),
    ]);

    set_route_readiness(
        &process.route_readiness,
        1,
        "resync_required",
        Some("gap".into()),
    );

    assert_eq!(
        resync_targets(&process.route_readiness),
        vec![(1, Some("okx.principal.main".into()))]
    );
}

struct ReconnectingStream {
    state: ConnectionState,
    calls: usize,
    reconnects: Arc<AtomicUsize>,
}

impl ReconnectingStream {
    fn new(reconnects: Arc<AtomicUsize>) -> Self {
        let identity = ConnectionDescriptor::new(
            "execution.test.reconnecting-stream",
            ParticipantRef::new(ParticipantKind::Exchange, "fixture").unwrap(),
            "execution-stream",
        )
        .unwrap();
        Self {
            state: ConnectionState::new(identity),
            calls: 0,
            reconnects,
        }
    }
}

impl OrderEventSource for ReconnectingStream {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.reconnects.fetch_add(1, Ordering::SeqCst);
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
        self.calls += 1;
        match self.calls {
            1 => Err(IntegrationError::Transport(
                "simulated private stream disconnect".into(),
            )),
            2 => {
                let event = ExternalExecutionEvent {
                    order_id: kairos_primitives::OrderId::new("local-recovered-order").unwrap(),
                    symbol: kairos_primitives::Symbol::new("BTCUSDT").unwrap(),
                    status: kairos_primitives::OrderStatus::Filled,
                    side: Some(OrderSide::Buy),
                    order_type: None,
                    quantity: None,
                    limit_price: None,
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: None,
                    fill_price: None,
                    execution_id: Some(
                        kairos_primitives::FillId::new("recovered-event-1").unwrap(),
                    ),
                    fee_currency: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: 42.into(),
                    reason: "received after reconnect".into(),
                };
                Ok(Some(ExternalEventEnvelope {
                    participant: self.state.identity.participant.clone(),
                    binding_id: self.state.identity.binding_id.clone(),
                    channel_id: "execution.test.reconnecting-stream.orders".into(),
                    channel_epoch: self.reconnects.load(Ordering::SeqCst) as u64 + 1,
                    provider_event_id: Some("recovered-event-1".into()),
                    provider_sequence: None,
                    observed_at_unix_nanos: event.occurred_at_unix_nanos,
                    received_at_unix_nanos: event.occurred_at_unix_nanos,
                    payload: event,
                }))
            }
            _ => Ok(None),
        }
    }
}

#[test]
fn stream_consumer_reconnects_after_disconnect_and_delivers_next_event() {
    let reconnects = Arc::new(AtomicUsize::new(0));
    let stream = ReconnectingStream::new(Arc::clone(&reconnects));
    let application = ExecutionApplication::assemble_for_test_with_query_and_stream(
        "execution",
        None,
        None,
        Some(Box::new(stream)),
        None,
    )
    .expect("fixture application");
    let mut process = ExecutionProcess::new(application, PathBuf::from("/tmp/execution-test.sock"));
    let (sender, receiver) = mpsc::sync_channel(1);
    let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let handle = process
        .start_stream_consumer(sender, Arc::clone(&stop), Arc::clone(&process.metrics))
        .expect("stream task");
    let event = receiver
        .recv_timeout(std::time::Duration::from_secs(2))
        .expect("event after reconnect");
    stop.store(true, Ordering::Release);
    handle.join().expect("stream task join");
    assert_eq!(event.event_id, "recovered-event-1");
    assert_eq!(reconnects.load(Ordering::SeqCst), 1);
}

struct AsyncReconnectingStream {
    calls: usize,
    reconnects: Arc<AtomicUsize>,
    resync_before_reconnect: bool,
}

impl AsyncOrderEventSource for AsyncReconnectingStream {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        Ok(())
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        Ok(())
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.reconnects.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: ConnectionLifecycle::Ready,
            healthy: true,
            authenticated: true,
            last_error: None,
        }
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        self.calls += 1;
        match self.calls {
            1 if self.resync_before_reconnect => Err(IntegrationError::Backpressure(
                "simulated async stream overflow".into(),
            )),
            1 => Err(IntegrationError::Transport(
                "simulated async stream disconnect".into(),
            )),
            2 => {
                let event = ExternalExecutionEvent {
                    order_id: kairos_primitives::OrderId::new("async-recovered-order").unwrap(),
                    symbol: kairos_primitives::Symbol::new("BTCUSDT").unwrap(),
                    status: kairos_primitives::OrderStatus::Filled,
                    side: Some(OrderSide::Buy),
                    order_type: None,
                    quantity: None,
                    limit_price: None,
                    filled_quantity: None,
                    remaining_quantity: None,
                    fill_quantity: None,
                    fill_price: None,
                    execution_id: Some(
                        kairos_primitives::FillId::new("async-recovered-event").unwrap(),
                    ),
                    fee_currency: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: 43.into(),
                    reason: "received by async runtime".into(),
                };
                Ok(ExternalEventEnvelope {
                    participant: kairos_integration::application::ParticipantRef::new(
                        kairos_integration::application::ParticipantKind::Exchange,
                        "test",
                    )
                    .unwrap(),
                    binding_id: "execution.test.async-stream".into(),
                    channel_id: "execution.test.async-stream.orders".into(),
                    channel_epoch: 2,
                    provider_event_id: Some("async-recovered-event".into()),
                    provider_sequence: None,
                    observed_at_unix_nanos: event.occurred_at_unix_nanos,
                    received_at_unix_nanos: event.occurred_at_unix_nanos,
                    payload: event,
                })
            }
            _ => std::future::pending().await,
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn async_stream_consumer_awaits_without_polling_and_reconnects() {
    let reconnects = Arc::new(AtomicUsize::new(0));
    let application = ExecutionApplication::assemble_for_test("execution", None, None)
        .expect("fixture application");
    let mut process =
        ExecutionProcess::new(application, PathBuf::from("/tmp/execution-async-test.sock"))
            .with_async_execution_stream(Some(AsyncReconnectingStream {
                calls: 0,
                reconnects: Arc::clone(&reconnects),
                resync_before_reconnect: false,
            }));
    let (sender, receiver) = mpsc::sync_channel(1);
    let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
    let mut handles =
        process.start_async_stream_consumers(sender, shutdown_rx, Arc::clone(&process.metrics));
    assert_eq!(handles.len(), 1);
    let handle = handles.pop().expect("async stream task");
    let event = tokio::task::spawn_blocking(move || {
        receiver.recv_timeout(std::time::Duration::from_secs(2))
    })
    .await
    .unwrap()
    .expect("event after async reconnect");
    shutdown.send(true).unwrap();
    handle.await.unwrap();
    assert_eq!(event.event_id, "async-recovered-event");
    assert_eq!(reconnects.load(Ordering::SeqCst), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn resync_error_waits_for_reconciliation_barrier_before_reconnect() {
    let reconnects = Arc::new(AtomicUsize::new(0));
    let application = ExecutionApplication::assemble_for_test("execution", None, None)
        .expect("fixture application");
    let mut process = ExecutionProcess::new(
        application,
        PathBuf::from("/tmp/execution-resync-test.sock"),
    )
    .with_async_execution_stream(Some(AsyncReconnectingStream {
        calls: 0,
        reconnects: Arc::clone(&reconnects),
        resync_before_reconnect: true,
    }));
    let readiness = Arc::clone(&process.route_readiness);
    let (sender, receiver) = mpsc::sync_channel(1);
    let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
    let mut handles =
        process.start_async_stream_consumers(sender, shutdown_rx, Arc::clone(&process.metrics));
    let handle = handles.pop().expect("async stream task");

    tokio::time::timeout(std::time::Duration::from_secs(1), async {
        while !super::resync_required(&readiness) {
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("stream must request reconciliation");
    assert_eq!(reconnects.load(Ordering::SeqCst), 0);

    super::release_recovery_barrier(&readiness);
    let event = tokio::task::spawn_blocking(move || {
        receiver.recv_timeout(std::time::Duration::from_secs(2))
    })
    .await
    .unwrap()
    .expect("event after reconciliation and reconnect");
    shutdown.send(true).unwrap();
    handle.await.unwrap();
    assert_eq!(event.event_id, "async-recovered-event");
    assert_eq!(reconnects.load(Ordering::SeqCst), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn resync_barrier_isolates_the_failed_route() {
    let failed_route_reconnects = Arc::new(AtomicUsize::new(0));
    let healthy_route_reconnects = Arc::new(AtomicUsize::new(0));
    let application = ExecutionApplication::assemble_for_test("execution", None, None)
        .expect("fixture application");
    let mut process = ExecutionProcess::new(
        application,
        PathBuf::from("/tmp/execution-route-isolation-test.sock"),
    )
    .with_async_execution_routes(vec![
        ExecutionAsyncRoute::new(
            "binance",
            true,
            AsyncReconnectingStream {
                calls: 0,
                reconnects: Arc::clone(&failed_route_reconnects),
                resync_before_reconnect: true,
            },
        )
        .with_binding_id("binance.principal.main"),
        ExecutionAsyncRoute::new(
            "okx",
            true,
            AsyncReconnectingStream {
                calls: 0,
                reconnects: Arc::clone(&healthy_route_reconnects),
                resync_before_reconnect: false,
            },
        )
        .with_binding_id("okx.principal.main"),
    ]);
    let readiness = Arc::clone(&process.route_readiness);
    let (sender, receiver) = mpsc::sync_channel(2);
    let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
    let handles =
        process.start_async_stream_consumers(sender, shutdown_rx, Arc::clone(&process.metrics));

    tokio::time::timeout(std::time::Duration::from_secs(1), async {
        while super::route_status(&readiness, 0) != Some("resync_required") {
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("failed route must wait at its reconciliation barrier");

    let event = tokio::task::spawn_blocking(move || {
        receiver.recv_timeout(std::time::Duration::from_secs(2))
    })
    .await
    .unwrap()
    .expect("healthy route must keep delivering");
    assert_eq!(event.event_id, "async-recovered-event");
    assert_eq!(failed_route_reconnects.load(Ordering::SeqCst), 0);
    assert_eq!(healthy_route_reconnects.load(Ordering::SeqCst), 1);
    assert_eq!(super::route_status(&readiness, 0), Some("resync_required"));
    assert_eq!(super::route_status(&readiness, 1), Some("ready"));
    assert_eq!(
        resync_targets(&readiness),
        vec![(0, Some("binance.principal.main".into()))]
    );

    shutdown.send(true).unwrap();
    for handle in handles {
        handle.await.unwrap();
    }
}

struct UnreadyAsyncStream;

impl AsyncOrderEventSource for UnreadyAsyncStream {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        Err(IntegrationError::Authentication(
            "fixture login rejected".into(),
        ))
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        Ok(())
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: ConnectionLifecycle::Degraded,
            healthy: false,
            authenticated: false,
            last_error: Some("fixture login rejected".into()),
        }
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        std::future::pending().await
    }
}

#[tokio::test]
async fn required_async_stream_must_authenticate_before_process_readiness() {
    let application = ExecutionApplication::assemble_for_test("execution", None, None)
        .expect("fixture application");
    let mut process = ExecutionProcess::new(
        application,
        PathBuf::from("/tmp/execution-unready-test.sock"),
    )
    .with_async_execution_stream(Some(UnreadyAsyncStream));

    let error = process
        .connect_async_execution_streams()
        .await
        .expect_err("failed provider authentication must block readiness");
    assert!(matches!(error, IntegrationError::Authentication(_)));
}

#[tokio::test]
async fn optional_async_stream_failure_degrades_without_blocking_readiness() {
    let application = ExecutionApplication::assemble_for_test("execution", None, None)
        .expect("fixture application");
    let mut process = ExecutionProcess::new(
        application,
        PathBuf::from("/tmp/execution-degraded-test.sock"),
    )
    .with_async_execution_routes(vec![ExecutionAsyncRoute::new(
        "optional-route",
        false,
        UnreadyAsyncStream,
    )]);

    process
        .connect_async_execution_streams()
        .await
        .expect("optional route must not prevent process startup");
    let (status, routes) = process_readiness(&process.route_readiness);
    assert_eq!(status, "degraded");
    assert_eq!(routes.len(), 1);
    assert_eq!(routes[0].route_id, "optional-route");
    assert_eq!(routes[0].status, "degraded");
}
