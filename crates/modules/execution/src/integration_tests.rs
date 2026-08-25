//! White-box behavior tests for dependency failure, recovery, and persistence.
//!
//! These tests intentionally exercise private application wiring. Keeping them in
//! the crate avoids turning test doubles into a public Application API.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use kairos_conflux::{
    BlockingOrderCommand as OrderCommand, BlockingOrderQuery as OrderQuery, CommandOutcome,
    Conflux, ConfluxConfig, ConfluxSystem, ConnectionKey, ExternalOrder, ExternalOrderQuery,
    IndeterminateCommand, IntegrationError, JsonRpcRuntimeConfig, MmapOutputDeclaration,
    OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderType as ConnectionOrderType,
    ParticipantInstrumentRef, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    ShutdownMode, TimeInForce,
};
use kairos_execution::application::{
    BacktestApplication, BacktestEquityPoint, BacktestFill, BacktestRequest, CancelOrder,
    ExecuteStrategyIntent, ExecutionAuditQuery, ExecutionFillReport, RefreshQuoteIntent,
    RemoteOrderUpdate, RiskCommandFailure, SubmitOrder,
};
use kairos_execution::composition::{
    ExecutionConnectionOptions, FileExecutionStore, SimulatedRiskBehavior,
    SimulatedRiskReconciliation, SimulationConfig, SimulationOrderRequest, SimulationOrderStatus,
    SqlxExecutionAudit, SqlxExecutionStore, compose_order_entry, configure_simulated_risk,
};
use kairos_execution::{
    AlgorithmActionKind, AlgorithmActionStatus, AlgorithmExecutionStyle, AlgorithmRunStatus,
    ExecutionApplication, ExecutionError, ExecutionEvent, ExecutionOrderStatus, HedgePolicy,
    MarketObservation, OrderSide, OrderType, Quote, SplitOrderPolicy, UnknownRemoteOrderResolution,
};
use kairos_execution_contract::{
    ExecutionControlRpcServer, ExecutionViewKey, ExecutionViewKind, ExecutionViewPublisher,
};
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::decimal::{Money, Price, Quantity};
use kairos_primitives::execution::{
    ClientOrderId, ExecutionRouteId, FillId, IntentId, LegId, OrderId,
};
use kairos_primitives::reference::{Currency, InstrumentId, MarketId, Symbol};
use kairos_primitives::time::{DurationNanos, UnixNanos};
use secrecy::SecretString;

use crate::services::audit::{ExecutionAudit, MemoryExecutionAudit};
use crate::services::gateway::ExecutionConnectionPlan;
use crate::services::persistence::ExecutionStateStore;

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
        quantity: kairos_primitives::decimal::Quantity::new(quantity, 0).unwrap(),
        price: Price::new(price, 0).unwrap(),
        fee: kairos_primitives::decimal::Money::new(fee, 0).unwrap(),
        fee_currency: None,
        occurred_at_unix_nanos: occurred_at_unix_nanos.map(Into::into),
        execution_market_id: None,
        reported_broker_id: None,
        execution_channel: None,
        order_entry_symbol: None,
        remote_order_id: None,
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
        strategy_id: Some(kairos_primitives::runtime::StrategyId::new("strategy").unwrap()),
        account_id: AccountId::new(account_id).unwrap(),
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new(instrument_id).unwrap(),
        market_id: market_id.map(|value| MarketId::new(value).unwrap()),
        execution_route_id: Some(ExecutionRouteId::new("execution-route:test").unwrap()),
        side,
        order_type,
        quantity: Quantity::new(quantity, 0).unwrap(),
        limit_price: limit_price.map(|value| Price::new(value, 0).unwrap()),
        options: Default::default(),
        submitted_at_unix_nanos: None,
    }
}

fn strategy_intent(
    intent_id: &str,
    quantity: i64,
    limit_price: Option<i64>,
) -> ExecuteStrategyIntent {
    ExecuteStrategyIntent {
        intent_id: IntentId::new(intent_id).unwrap(),
        strategy_decision_id: Some(format!("decision:{intent_id}")),
        strategy_id: "strategy".into(),
        launch_id: "launch".into(),
        instance_id: "instance".into(),
        instrument_id: InstrumentId::new("BTCUSDT").unwrap(),
        market_id: None,
        execution_route_id: Some(ExecutionRouteId::new("execution-route:test").unwrap()),
        account_ids: vec![AccountId::new("main").unwrap()],
        segment_key: SegmentKey::new("spot").unwrap(),
        target_quantity: Quantity::new(quantity, 0).unwrap(),
        limit_price: limit_price.map(|value| Price::new(value, 0).unwrap()),
        source_snapshot_id: None,
        source_event_sequence: None,
        source_event_time_unix_nanos: None,
        reason: String::new(),
        intent_type: Default::default(),
        algorithm: kairos_execution::ExecutionAlgorithmPolicy::Immediate,
        completion_policy: Default::default(),
        failure_policy: Default::default(),
        legs: Vec::new(),
        deadline_unix_nanos: None,
        min_edge_bps: None,
        max_slippage_bps: None,
        estimated_fee_bps: None,
        minimum_net_credit: None,
        maximum_loss: None,
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
        execution_route_id: Some(ExecutionRouteId::new("execution-route:test").unwrap()),
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

impl OrderCommand for FailingOrderEntry {
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

struct SecondSubmitIndeterminate {
    submissions: u32,
}

impl OrderCommand for SecondSubmitIndeterminate {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.submissions = self.submissions.saturating_add(1);
        if self.submissions == 2 {
            return Ok(CommandOutcome::Indeterminate(
                IndeterminateCommand::may_have_been_sent("hedge response was lost"),
            ));
        }
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            remote_order_id: Some(
                kairos_primitives::integration::RemoteOrderId::new(format!(
                    "remote:{}",
                    request.order_id
                ))
                .unwrap(),
            ),
            filled_quantity: None,
            occurred_at_unix_nanos: 1.into(),
            reason: String::new(),
        }))
    }

    fn cancel_order(
        &mut self,
        _request: &OrderEntryRequest,
        _remote_order_id: &str,
        _at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Err(IntegrationError::Transport(
            "cancel is not used by this fixture".into(),
        ))
    }
}

struct HedgeFailsThenUnwindConfirms {
    submissions: u32,
    unwind_indeterminate: bool,
}

struct PrimaryHedgeFailsThenFallback {
    submissions: u32,
    fallback_outcome: FallbackOutcome,
}

#[derive(Clone, Copy)]
enum FallbackOutcome {
    Confirmed,
    Failed,
    Indeterminate,
}

struct CrashAfterStagingUnwindStore {
    snapshot: Arc<Mutex<Option<kairos_execution::application::ExecutionSnapshot>>>,
    fail_once: bool,
}

struct CrashAfterStagingFallbackStore {
    snapshot: Arc<Mutex<Option<kairos_execution::application::ExecutionSnapshot>>>,
    fail_once: bool,
}

impl ExecutionStateStore for CrashAfterStagingFallbackStore {
    fn load(&mut self) -> Result<Option<kairos_execution::application::ExecutionSnapshot>, String> {
        Ok(self.snapshot.lock().unwrap().clone())
    }

    fn save(
        &mut self,
        snapshot: &kairos_execution::application::ExecutionSnapshot,
    ) -> Result<(), String> {
        *self.snapshot.lock().unwrap() = Some(snapshot.clone());
        let staged_fallback = snapshot.intents.iter().any(|state| {
            state.pending_orders.iter().any(|order| {
                order
                    .execution_route_id
                    .as_ref()
                    .is_some_and(|route_id| route_id.as_str() == "execution-route:fallback")
            })
        });
        if self.fail_once && staged_fallback {
            self.fail_once = false;
            return Err("fixture stops after the fallback action and request are durable".into());
        }
        Ok(())
    }
}

impl ExecutionStateStore for CrashAfterStagingUnwindStore {
    fn load(&mut self) -> Result<Option<kairos_execution::application::ExecutionSnapshot>, String> {
        Ok(self.snapshot.lock().unwrap().clone())
    }

    fn save(
        &mut self,
        snapshot: &kairos_execution::application::ExecutionSnapshot,
    ) -> Result<(), String> {
        *self.snapshot.lock().unwrap() = Some(snapshot.clone());
        let staged_unwind = snapshot.intents.iter().any(|state| {
            state
                .pending_orders
                .iter()
                .any(|order| order.order_id.as_str().contains(":unwind:decision:"))
        });
        if self.fail_once && staged_unwind {
            self.fail_once = false;
            return Err("fixture stops after the unwind action and request are durable".into());
        }
        Ok(())
    }
}

struct SharedSnapshotStore {
    snapshot: Arc<Mutex<Option<kairos_execution::application::ExecutionSnapshot>>>,
}

impl ExecutionStateStore for SharedSnapshotStore {
    fn load(&mut self) -> Result<Option<kairos_execution::application::ExecutionSnapshot>, String> {
        Ok(self.snapshot.lock().unwrap().clone())
    }

    fn save(
        &mut self,
        snapshot: &kairos_execution::application::ExecutionSnapshot,
    ) -> Result<(), String> {
        *self.snapshot.lock().unwrap() = Some(snapshot.clone());
        Ok(())
    }
}

impl OrderCommand for HedgeFailsThenUnwindConfirms {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.submissions = self.submissions.saturating_add(1);
        if self.submissions == 2 {
            return Err(IntegrationError::Transport(
                "fixture proves the taker hedge was not sent".into(),
            ));
        }
        if self.submissions >= 3 {
            assert!(request.order_id.as_str().contains(":unwind:decision:"));
            assert_eq!(request.side, kairos_conflux::OrderSide::Sell);
            assert_eq!(request.order_type, ConnectionOrderType::Limit);
            assert_eq!(
                request.quantity.mantissa,
                if self.submissions == 3 { 2 } else { 1 }
            );
            assert_eq!(request.limit_price.unwrap().mantissa, 99);
            assert_eq!(
                request.options.time_in_force,
                Some(TimeInForce::ImmediateOrCancel)
            );
            assert_eq!(request.options.post_only, Some(false));
            if self.unwind_indeterminate {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent("unwind response was lost"),
                ));
            }
        }
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            remote_order_id: Some(
                kairos_primitives::integration::RemoteOrderId::new(format!(
                    "remote:{}",
                    request.order_id
                ))
                .unwrap(),
            ),
            filled_quantity: None,
            occurred_at_unix_nanos: u64::from(self.submissions).into(),
            reason: String::new(),
        }))
    }

    fn cancel_order(
        &mut self,
        _request: &OrderEntryRequest,
        _remote_order_id: &str,
        _at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Err(IntegrationError::Transport(
            "cancel is not used by this fixture".into(),
        ))
    }
}

impl OrderCommand for PrimaryHedgeFailsThenFallback {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.submissions = self.submissions.saturating_add(1);
        if self.submissions == 2 {
            return Err(IntegrationError::Transport(
                "fixture proves the primary hedge was not sent".into(),
            ));
        }
        if self.submissions == 3 {
            match self.fallback_outcome {
                FallbackOutcome::Failed => {
                    return Err(IntegrationError::Transport(
                        "fixture proves the fallback hedge was not sent".into(),
                    ));
                },
                FallbackOutcome::Indeterminate => {
                    return Ok(CommandOutcome::Indeterminate(
                        IndeterminateCommand::may_have_been_sent(
                            "fallback hedge response was lost",
                        ),
                    ));
                },
                FallbackOutcome::Confirmed => {},
            }
        }
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            remote_order_id: Some(
                kairos_primitives::integration::RemoteOrderId::new(format!(
                    "remote:{}",
                    request.order_id
                ))
                .unwrap(),
            ),
            filled_quantity: None,
            occurred_at_unix_nanos: u64::from(self.submissions).into(),
            reason: String::new(),
        }))
    }

    fn cancel_order(
        &mut self,
        _request: &OrderEntryRequest,
        _remote_order_id: &str,
        _at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Err(IntegrationError::Transport(
            "cancel is not used by this fixture".into(),
        ))
    }
}

struct InvalidAcknowledgementOrderEntry;

impl OrderCommand for InvalidAcknowledgementOrderEntry {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            remote_order_id: None,
            filled_quantity: None,
            occurred_at_unix_nanos: 1.into(),
            reason: String::new(),
        }))
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        _remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            remote_order_id: None,
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos.into(),
            reason: String::new(),
        }))
    }
}

fn application(path: &std::path::Path) -> ExecutionApplication {
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        broker_id: "simulated".into(),
        execution_channel: "spot".into(),
        trading_mode: None,
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        instruments: Vec::new(),
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
        initial_margin_rate_bps: None,
        margin_rule_id: None,
    })
    .unwrap();
    let mut application = ExecutionApplication::assemble_for_test(
        "execution",
        Some(connection),
        Some(Box::new(FileExecutionStore::new(path))),
    )
    .unwrap();
    application.configure_execution_route(
        crate::application::ExecutionRouteCandidate {
            route_id: ExecutionRouteId::new("execution-route:test").unwrap(),
            account_id: None,
            segment_key: None,
            instrument_id: None,
            market_id: None,
            broker_id: BrokerId::new("simulated").unwrap(),
            execution_channel: kairos_primitives::execution::ExecutionChannelCode::new("spot")
                .unwrap(),
            order_entry_symbol: kairos_primitives::execution::OrderEntrySymbol::new("BTCUSDT")
                .unwrap(),
            supported_order_types: vec![
                crate::application::OrderType::Market,
                crate::application::OrderType::Limit,
            ],
            supported_options: vec![
                "time_in_force".into(),
                "reduce_only".into(),
                "post_only".into(),
                "position_side".into(),
                "quote_asset".into(),
                "wallet_type".into(),
                "trading_session".into(),
                "tokenize".into(),
            ],
            ready: true,
            initial_margin_rate_bps: Some(10_000),
            margin_rule_id: Some("test:fully-funded".into()),
        },
        ParticipantInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "simulated").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("spot").unwrap()),
            "BTCUSDT",
        )
        .unwrap(),
    );
    attach_simulated_risk(&mut application, test_risk());
    application
}

fn configure_test_access(application: &mut ExecutionApplication) {
    application.configure_execution_route(
        crate::application::ExecutionRouteCandidate {
            route_id: ExecutionRouteId::new("execution-route:test").unwrap(),
            account_id: None,
            segment_key: None,
            instrument_id: None,
            market_id: None,
            broker_id: BrokerId::new("simulated").unwrap(),
            execution_channel: kairos_primitives::execution::ExecutionChannelCode::new("spot")
                .unwrap(),
            order_entry_symbol: kairos_primitives::execution::OrderEntrySymbol::new("BTCUSDT")
                .unwrap(),
            supported_order_types: vec![
                crate::application::OrderType::Market,
                crate::application::OrderType::Limit,
            ],
            supported_options: vec![
                "time_in_force".into(),
                "reduce_only".into(),
                "post_only".into(),
                "position_side".into(),
                "quote_asset".into(),
                "wallet_type".into(),
                "trading_session".into(),
                "tokenize".into(),
            ],
            ready: true,
            initial_margin_rate_bps: Some(10_000),
            margin_rule_id: Some("test:fully-funded".into()),
        },
        ParticipantInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "simulated").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("spot").unwrap()),
            "BTCUSDT",
        )
        .unwrap(),
    );
}

fn configure_fallback_access(application: &mut ExecutionApplication) {
    application.configure_execution_route(
        crate::application::ExecutionRouteCandidate {
            route_id: ExecutionRouteId::new("execution-route:fallback").unwrap(),
            account_id: None,
            segment_key: None,
            instrument_id: None,
            market_id: None,
            broker_id: BrokerId::new("simulated-fallback").unwrap(),
            execution_channel: kairos_primitives::execution::ExecutionChannelCode::new("spot")
                .unwrap(),
            order_entry_symbol: kairos_primitives::execution::OrderEntrySymbol::new("BTCUSDT")
                .unwrap(),
            supported_order_types: vec![OrderType::Market],
            supported_options: vec!["post_only".into()],
            ready: true,
            initial_margin_rate_bps: Some(10_000),
            margin_rule_id: Some("test:fallback-fully-funded".into()),
        },
        ParticipantInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "simulated-fallback").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("spot").unwrap()),
            "BTCUSDT",
        )
        .unwrap(),
    );
}

#[test]
fn route_selection_rejects_an_instrument_mismatch_before_creating_order_state() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = application(&directory.path().join("execution.json"));
    app.configure_execution_route(
        crate::application::ExecutionRouteCandidate {
            route_id: ExecutionRouteId::new("execution-route:test").unwrap(),
            account_id: None,
            segment_key: None,
            instrument_id: Some(InstrumentId::new("BTCUSDT").unwrap()),
            market_id: None,
            broker_id: BrokerId::new("simulated").unwrap(),
            execution_channel: kairos_primitives::execution::ExecutionChannelCode::new("spot")
                .unwrap(),
            order_entry_symbol: kairos_primitives::execution::OrderEntrySymbol::new("BTCUSDT")
                .unwrap(),
            supported_order_types: vec![OrderType::Market, OrderType::Limit],
            supported_options: Vec::new(),
            ready: true,
            initial_margin_rate_bps: Some(10_000),
            margin_rule_id: Some("test:fully-funded".into()),
        },
        ParticipantInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "simulated").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("spot").unwrap()),
            "BTCUSDT",
        )
        .unwrap(),
    );
    let error = app
        .submit(submit_order(
            "wrong-route-instrument",
            None,
            "main",
            "ETHUSDT",
            OrderSide::Buy,
            OrderType::Market,
            1,
            None,
            None,
        ))
        .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("configured for instrument BTCUSDT")
    );
    assert!(app.orders(None).is_empty());
}

#[test]
fn route_selection_rejects_an_unsupported_order_type_before_creating_order_state() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = application(&directory.path().join("execution.json"));
    app.configure_execution_route(
        crate::application::ExecutionRouteCandidate {
            route_id: ExecutionRouteId::new("execution-route:test").unwrap(),
            account_id: None,
            segment_key: None,
            instrument_id: None,
            market_id: None,
            broker_id: BrokerId::new("simulated").unwrap(),
            execution_channel: kairos_primitives::execution::ExecutionChannelCode::new("spot")
                .unwrap(),
            order_entry_symbol: kairos_primitives::execution::OrderEntrySymbol::new("BTCUSDT")
                .unwrap(),
            supported_order_types: vec![OrderType::Market],
            supported_options: Vec::new(),
            ready: true,
            initial_margin_rate_bps: Some(10_000),
            margin_rule_id: Some("test:fully-funded".into()),
        },
        ParticipantInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "simulated").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("spot").unwrap()),
            "BTCUSDT",
        )
        .unwrap(),
    );
    assert!(
        app.available_execution_routes(&crate::application::ExecutionRouteQuery {
            order_type: Some(OrderType::Limit),
            ..Default::default()
        })
        .is_empty()
    );
    let error = app
        .submit(submit_order(
            "unsupported-order-type",
            None,
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Limit,
            1,
            Some(100),
            None,
        ))
        .unwrap_err();
    assert!(error.to_string().contains("does not support Limit orders"));
    assert!(app.orders(None).is_empty());
}

#[test]
fn selected_route_snapshot_is_persisted_with_the_order() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.submit(submit_order(
        "selected-route-order",
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

    let selected = app.orders(None)[0].selected_route.clone().unwrap();
    let attempts = app.orders(None)[0].attempts.clone();
    assert_eq!(selected.route_id, "execution-route:test");
    assert_eq!(selected.broker_id, "simulated");
    assert_eq!(selected.execution_channel, "spot");
    assert_eq!(selected.order_entry_symbol, "BTCUSDT");
    assert_eq!(attempts.len(), 1);
    assert_eq!(attempts[0].selected_route, selected);

    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(restored.orders(None)[0].selected_route, Some(selected));
    assert_eq!(restored.orders(None)[0].attempts, attempts);
}

fn attach_simulated_risk(application: &mut ExecutionApplication, behavior: SimulatedRiskBehavior) {
    configure_simulated_risk(application, behavior, 16).unwrap();
}

fn test_risk() -> SimulatedRiskBehavior {
    SimulatedRiskBehavior::default()
}

fn uncertain_authorization_risk() -> SimulatedRiskBehavior {
    SimulatedRiskBehavior {
        authorization_failure: Some(RiskCommandFailure::indeterminate(
            "risk authorization response was lost",
        )),
        reconciliation: SimulatedRiskReconciliation::Missing,
        ..SimulatedRiskBehavior::default()
    }
}

fn not_sent_authorization_risk() -> SimulatedRiskBehavior {
    SimulatedRiskBehavior {
        authorization_failure: Some(RiskCommandFailure::NotSent(
            "risk socket was unavailable before send".into(),
        )),
        reconciliation: SimulatedRiskReconciliation::Missing,
        ..SimulatedRiskBehavior::default()
    }
}

fn insufficient_funding_risk() -> SimulatedRiskBehavior {
    SimulatedRiskBehavior {
        authorization_failure: Some(RiskCommandFailure::DeferredInsufficientFunding {
            requirement: kairos_execution::application::ExecutionFundingRequirement {
                required_margin: Money::new(100, 0).unwrap(),
                available_margin: Money::new(40, 0).unwrap(),
                shortfall: Money::new(60, 0).unwrap(),
                margin_rule_id: "binance-usdm-initial-margin:v1".into(),
                risk_decision_id: kairos_primitives::risk::DecisionId::new("risk-decision:funding")
                    .unwrap(),
                risk_policy_version: 7.into(),
                account_snapshot_watermark: 11.into(),
                broker: kairos_primitives::account::BrokerId::new("binance").unwrap(),
                segment: kairos_primitives::account::SegmentKey::new("usd-m").unwrap(),
                collateral_asset: kairos_primitives::reference::Currency::new("USDT").unwrap(),
            },
        }),
        ..SimulatedRiskBehavior::default()
    }
}

fn uncertain_release_risk() -> SimulatedRiskBehavior {
    SimulatedRiskBehavior {
        release_failure: Some(RiskCommandFailure::indeterminate(
            "risk release response was lost",
        )),
        ..SimulatedRiskBehavior::default()
    }
}

fn recovery_risk(
    observed_status: Option<kairos_execution::application::RiskReservationSagaStatus>,
    observed_event_sequence: u64,
) -> SimulatedRiskBehavior {
    SimulatedRiskBehavior {
        reconciliation: match observed_status {
            Some(status) => SimulatedRiskReconciliation::Observed {
                status,
                event_sequence: observed_event_sequence,
            },
            None => SimulatedRiskReconciliation::Missing,
        },
        ..SimulatedRiskBehavior::default()
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

impl OrderQuery for RecoveryOrderQuery {
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

fn decimal(value: &str) -> kairos_conflux::DecimalValue {
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    kairos_conflux::DecimalValue {
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

fn quantity(value: &str) -> kairos_primitives::decimal::Quantity {
    value.parse().unwrap()
}

fn price(value: &str) -> kairos_primitives::decimal::Price {
    value.parse().unwrap()
}

fn fill_id(value: &str) -> FillId {
    FillId::new(value).unwrap()
}

fn currency(value: &str) -> Currency {
    Currency::new(value).unwrap()
}

fn money(value: &str) -> kairos_primitives::decimal::Money {
    value.parse().unwrap()
}

#[test]
fn normalized_remote_execution_event_reconciles_a_fill() {
    let directory = tempfile::tempdir().unwrap();
    let state = directory.path().join("execution.json");
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        broker_id: "simulated".into(),
        execution_channel: "spot".into(),
        trading_mode: None,
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        instruments: Vec::new(),
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
        initial_margin_rate_bps: None,
        margin_rule_id: None,
    })
    .unwrap();
    let mut app = ExecutionApplication::assemble_for_test_with_query(
        "execution",
        Some(connection),
        None,
        Some(Box::new(FileExecutionStore::new(&state))),
    )
    .unwrap();
    configure_test_access(&mut app);
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
    let order = app
        .apply_remote_execution_event(RemoteOrderUpdate {
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
        })
        .unwrap();
    assert_eq!(order.status, ExecutionOrderStatus::Filled);
    assert_eq!(app.snapshot().fills.len(), 1);
}

#[test]
fn remote_query_reconciliation_persists_unknown_order_once() {
    let directory = tempfile::tempdir().unwrap();
    let state = directory.path().join("execution.json");
    let remote = ExternalOrder {
        connection_key: kairos_conflux::ConnectionKey::new("execution.fixture.query").unwrap(),
        order_id: OrderId::new("exchange-unknown-1").unwrap(),
        remote_order_id: kairos_primitives::integration::RemoteOrderId::new("exchange-unknown-1")
            .unwrap(),
        client_order_id: None,
        symbol: Symbol::new("BTCUSDT").unwrap(),
        side: kairos_conflux::OrderSide::Buy,
        order_type: kairos_conflux::OrderType::Limit,
        status: kairos_primitives::integration::OrderStatus::Filled,
        quantity: decimal("1"),
        filled_quantity: decimal("1"),
        average_fill_price: Some(decimal("100")),
        occurred_at_unix_nanos: Some(UnixNanos::from(42_000_000)),
    };
    let mut app = ExecutionApplication::assemble_for_test_with_query(
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
        connection_key: kairos_conflux::ConnectionKey::new("execution.fixture.query").unwrap(),
        order_id: OrderId::new("exchange-recovered-fill").unwrap(),
        remote_order_id: kairos_primitives::integration::RemoteOrderId::new(
            "exchange-recovered-fill",
        )
        .unwrap(),
        client_order_id: Some(ClientOrderId::new("local-recovered-fill").unwrap()),
        symbol: Symbol::new("BTCUSDT").unwrap(),
        side: kairos_conflux::OrderSide::Buy,
        order_type: kairos_conflux::OrderType::Limit,
        status: kairos_primitives::integration::OrderStatus::Filled,
        quantity: decimal("1"),
        filled_quantity: decimal("1"),
        average_fill_price: Some(decimal("100")),
        occurred_at_unix_nanos: Some(UnixNanos::from(42_000_000)),
    };
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        broker_id: "simulated".into(),
        execution_channel: "spot".into(),
        trading_mode: None,
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        instruments: Vec::new(),
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
        initial_margin_rate_bps: None,
        margin_rule_id: None,
    })
    .unwrap();
    let mut app = ExecutionApplication::assemble_for_test_with_query(
        "execution",
        Some(connection),
        Some(Box::new(RecoveryOrderQuery::new(vec![remote]))),
        Some(Box::new(FileExecutionStore::new(&state))),
    )
    .unwrap();
    configure_test_access(&mut app);
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
fn remote_fill_preserves_fee_payment_currency() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = application(&directory.path().join("execution.json"));
    app.submit(submit_order(
        "fee-currency-order",
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

    app.apply_remote_execution_event(RemoteOrderUpdate {
        order_id: order_id("fee-currency-order"),
        symbol: symbol("BTCUSDT"),
        status: ExecutionOrderStatus::Filled,
        fill_quantity: Some(quantity("1")),
        fill_price: Some(price("100")),
        execution_id: Some(fill_id("fee-currency-fill")),
        fee_currency: Some(currency("BNB")),
        fee_amount: Some(money("0.01")),
        occurred_at_unix_nanos: 42.into(),
        reason: String::new(),
    })
    .unwrap();

    assert_eq!(app.fills(None)[0].fee_currency.as_deref(), Some("BNB"));
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
    let mut app = ExecutionApplication::assemble_for_test_with_query(
        "execution",
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

    let restored = ExecutionApplication::assemble_for_test_with_query(
        "execution",
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
    assert_eq!(second.trace("order-1").len(), 4);
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
        broker_id: "simulated".into(),
        execution_channel: "spot".into(),
        trading_mode: None,
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        instruments: Vec::new(),
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
        initial_margin_rate_bps: None,
        margin_rule_id: None,
    })
    .unwrap();
    let mut first = ExecutionApplication::assemble_for_test(
        "execution",
        Some(connection),
        Some(Box::new(SqlxExecutionStore::new(&path).unwrap())),
    )
    .unwrap();
    configure_test_access(&mut first);
    attach_simulated_risk(&mut first, test_risk());
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
    let second = ExecutionApplication::assemble_for_test(
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
        broker_id: "simulated".into(),
        execution_channel: "spot".into(),
        trading_mode: None,
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        instruments: Vec::new(),
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
        initial_margin_rate_bps: None,
        margin_rule_id: None,
    })
    .unwrap();
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(connection),
        Some(Box::new(SqlxExecutionStore::new(&path).unwrap())),
    )
    .unwrap();
    configure_test_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());
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
    assert_eq!(pending.len(), 3);
    app.acknowledge_outbox(&pending.iter().map(|entry| entry.id).collect::<Vec<_>>())
        .unwrap();
    assert!(app.pending_outbox(10).unwrap().is_empty());
}

#[test]
fn not_sent_submission_is_persisted_as_failed_without_reconciliation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(FailingOrderEntry::new())),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    configure_test_access(&mut app);
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
    assert_eq!(app.trace("unknown-1").len(), 3);
    assert_eq!(
        app.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Released
    );
    assert_eq!(
        app.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Released
    );
}

#[test]
fn risk_authorization_identity_is_persisted_before_an_uncertain_command_outcome() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    attach_simulated_risk(&mut app, uncertain_authorization_risk());

    let error = app
        .submit(submit_order(
            "risk-uncertain",
            None,
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Limit,
            1,
            Some(100),
            None,
        ))
        .unwrap_err();
    assert!(error.to_string().contains("response was lost"));
    assert_eq!(app.orders(None)[0].status, ExecutionOrderStatus::Pending);
    assert_eq!(
        app.risk_reservations()[0].reservation_id,
        "execution:risk-uncertain"
    );
    assert_eq!(
        app.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Uncertain
    );
    assert!(app.commitments()[0].status.consumes_capacity());

    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(
        restored.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Uncertain
    );
    assert_eq!(
        restored.orders(None)[0].status,
        ExecutionOrderStatus::Pending
    );
}

#[test]
fn risk_authorization_not_sent_is_terminal_and_does_not_enter_uncertain_recovery() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    attach_simulated_risk(&mut app, not_sent_authorization_risk());

    let error = app
        .submit(submit_order(
            "risk-not-sent",
            None,
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Limit,
            1,
            Some(100),
            None,
        ))
        .unwrap_err();
    assert!(matches!(error, ExecutionError::Invalid(_)));
    assert_eq!(
        app.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Failed
    );
    assert!(!matches!(error, ExecutionError::Indeterminate(_)));
}

#[test]
fn insufficient_funding_is_audited_and_releases_unsubmitted_capacity() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    attach_simulated_risk(&mut app, insufficient_funding_risk());

    let error = app
        .submit(submit_order(
            "risk-funding-shortfall",
            None,
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Limit,
            1,
            Some(100),
            None,
        ))
        .unwrap_err();
    assert!(matches!(error, ExecutionError::Invalid(_)));
    assert_eq!(app.orders(None)[0].status, ExecutionOrderStatus::Rejected);
    assert!(!app.commitments()[0].status.consumes_capacity());
    let reservation = &app.risk_reservations()[0];
    assert_eq!(
        reservation.status,
        kairos_execution::application::RiskReservationSagaStatus::Failed
    );
    let requirement = reservation.funding_requirement.as_ref().unwrap();
    assert_eq!(requirement.shortfall, Money::new(60, 0).unwrap());
    assert_eq!(requirement.account_snapshot_watermark, 11.into());
    assert_eq!(requirement.segment.as_str(), "usd-m");
    assert_eq!(requirement.collateral_asset.as_str(), "USDT");

    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(
        restored.risk_reservations()[0]
            .funding_requirement
            .as_ref()
            .unwrap(),
        requirement
    );
}

#[test]
fn indeterminate_submission_is_explicit_and_persisted_for_reconciliation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(FailingOrderEntry::indeterminate())),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    configure_test_access(&mut app);

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
    assert_eq!(app.trace("indeterminate-1").len(), 3);
    assert_eq!(
        app.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Uncertain
    );
    assert_eq!(
        app.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Active
    );
    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(
        restored.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Uncertain
    );
}

#[test]
fn confirmed_submission_without_remote_identity_is_indeterminate() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(InvalidAcknowledgementOrderEntry)),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    configure_test_access(&mut app);

    let result = app.submit(submit_order(
        "missing-submit-identity",
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
    assert_eq!(app.orders(None)[0].status, ExecutionOrderStatus::Unknown);
    assert_eq!(
        app.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Uncertain
    );

    let cancel = app.cancel(CancelOrder {
        order_id: OrderId::new("missing-submit-identity").unwrap(),
        reason: "test".into(),
    });
    assert!(matches!(cancel, Err(ExecutionError::Invalid(_))));
    assert!(
        cancel
            .unwrap_err()
            .to_string()
            .contains("reconcile it before cancellation")
    );
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
    assert_eq!(app.trace("cancel-not-sent").len(), 3);
    assert_eq!(
        app.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Active
    );
    assert_eq!(
        app.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Active
    );
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
    assert_eq!(app.trace("cancel-indeterminate").len(), 4);
    assert_eq!(
        app.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Uncertain
    );
    assert_eq!(
        app.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Active
    );
}

#[test]
fn confirmed_cancel_without_remote_identity_is_indeterminate() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.submit(submit_order(
        "missing-cancel-identity",
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
    app.install_order_entry(Box::new(InvalidAcknowledgementOrderEntry));

    let result = app.cancel(CancelOrder {
        order_id: OrderId::new("missing-cancel-identity").unwrap(),
        reason: "test".into(),
    });

    assert!(matches!(result, Err(ExecutionError::Indeterminate(_))));
    assert_eq!(app.orders(None)[0].status, ExecutionOrderStatus::Unknown);
    assert_eq!(
        app.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Uncertain
    );
}

#[test]
fn uncertain_risk_release_is_persisted_after_confirmed_order_cancel() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    attach_simulated_risk(&mut app, uncertain_release_risk());
    app.submit(submit_order(
        "risk-release-uncertain",
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

    let error = app
        .cancel(CancelOrder {
            order_id: OrderId::new("risk-release-uncertain").unwrap(),
            reason: "test".into(),
        })
        .unwrap_err();
    assert!(error.to_string().contains("release response was lost"));
    assert_eq!(app.orders(None)[0].status, ExecutionOrderStatus::Canceled);
    assert_eq!(
        app.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Uncertain
    );
    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(
        restored.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Uncertain
    );
}

#[test]
fn restart_reconciles_uncertain_risk_saga_before_live_admission() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    attach_simulated_risk(&mut first, uncertain_authorization_risk());
    first
        .submit(submit_order(
            "risk-recovery",
            None,
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Limit,
            1,
            Some(100),
            None,
        ))
        .unwrap_err();

    let mut restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    attach_simulated_risk(
        &mut restored,
        recovery_risk(
            Some(kairos_execution::application::RiskReservationSagaStatus::Released),
            9,
        ),
    );
    restored.configure_live_trading(true, true);
    restored.recover_risk_reservations().unwrap();
    assert_eq!(
        restored.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Released
    );

    let verified = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(
        verified.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Released
    );
}

#[test]
fn missing_risk_mmap_evidence_keeps_live_admission_closed() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    attach_simulated_risk(&mut first, uncertain_authorization_risk());
    first
        .submit(submit_order(
            "risk-missing",
            None,
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Limit,
            1,
            Some(100),
            None,
        ))
        .unwrap_err();

    let mut restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    attach_simulated_risk(&mut restored, recovery_risk(None, 9));
    restored.configure_live_trading(true, true);
    assert!(restored.recover_risk_reservations().is_err());
    restored.complete_writer_reconciliation();
    let error = restored
        .prepare_submission(submit_order(
            "blocked-by-risk-recovery",
            None,
            "main",
            "BTCUSDT",
            OrderSide::Buy,
            OrderType::Limit,
            1,
            Some(100),
            None,
        ))
        .unwrap_err();
    assert!(error.to_string().contains("blocked by Risk recovery"));
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
    assert_eq!(
        first.intents()[0].intent.strategy_decision_id.as_deref(),
        Some("decision:strategy:intent:1")
    );
    assert_eq!(first.intent_events(None)[0].previous_status, None);
    assert!(
        first.intent_events(None).iter().all(
            |event| event.strategy_decision_id.as_deref() == Some("decision:strategy:intent:1")
        )
    );

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
fn algorithm_run_is_actor_owned_and_restored_with_order_progress() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    let state = first
        .submit_intent(strategy_intent("intent:algorithm-run", 3, None))
        .unwrap();
    let order_id = state.order_ids[0].clone();

    let run = first.algorithm_runs().pop().unwrap();
    assert_eq!(run.intent_id.as_str(), "intent:algorithm-run");
    assert_eq!(run.status, AlgorithmRunStatus::Running);
    assert_eq!(run.legs[0].committed_quantity, Quantity::new(3, 0).unwrap());
    assert_eq!(run.legs[0].filled_quantity, Quantity::ZERO);
    assert_eq!(run.actions.len(), 1);
    assert_eq!(run.actions[0].status, AlgorithmActionStatus::Completed);
    assert!(matches!(
        &run.actions[0].kind,
        AlgorithmActionKind::SubmitChild {
            order_id: action_order_id,
            ..
        } if action_order_id == &order_id
    ));

    let restored = application(&path);
    assert_eq!(restored.algorithm_runs(), vec![run]);

    first
        .record_fill(fill_report(
            "fill:algorithm-run",
            order_id.to_string(),
            3,
            100,
            0,
            Some(200),
        ))
        .unwrap();
    let completed = first.algorithm_runs().pop().unwrap();
    assert_eq!(completed.status, AlgorithmRunStatus::Completed);
    assert_eq!(completed.legs[0].committed_quantity, Quantity::ZERO);
    assert_eq!(
        completed.legs[0].filled_quantity,
        Quantity::new(3, 0).unwrap()
    );

    let restored = application(&path);
    assert_eq!(restored.algorithm_runs(), vec![completed]);
}

#[test]
fn historical_snapshot_without_algorithm_runs_remains_readable() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let app = application(&path);
    let mut encoded = serde_json::to_value(app.snapshot()).unwrap();
    encoded.as_object_mut().unwrap().remove("algorithm_runs");

    let restored: kairos_execution::ExecutionSnapshot = serde_json::from_value(encoded).unwrap();
    assert!(restored.algorithm_runs.is_empty());
}

#[test]
fn restart_reuses_the_durable_immediate_action_before_order_dispatch() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    first
        .accept_intent_with_idempotency_deferred(
            strategy_intent("intent:durable-action", 2, None),
            "durable-action-command".into(),
        )
        .unwrap();

    let due = first
        .take_due_intent_order(u64::MAX)
        .unwrap()
        .expect("one child is due");
    let action_id = due.action_id.clone();
    assert!(first.intents()[0].pending_orders.is_empty());
    assert_eq!(first.algorithm_runs()[0].actions.len(), 1);
    assert_eq!(
        first.algorithm_runs()[0].actions[0].status,
        AlgorithmActionStatus::Pending
    );
    drop(due);
    drop(first);

    let mut restored = application(&path);
    assert_eq!(restored.intents()[0].pending_orders.len(), 1);
    assert_eq!(restored.algorithm_runs()[0].actions[0].action_id, action_id);
    restored.advance_due_intent_orders(u64::MAX, 1).unwrap();

    let run = restored.algorithm_runs().pop().unwrap();
    assert_eq!(run.actions.len(), 1);
    assert_eq!(run.actions[0].action_id, action_id);
    assert_eq!(run.actions[0].status, AlgorithmActionStatus::Completed);
}

#[test]
fn restart_repairs_action_and_plan_identity_after_confirmed_dispatch() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    first
        .accept_intent_with_idempotency_deferred(
            strategy_intent("intent:dispatch-crash", 2, None),
            "dispatch-crash-command".into(),
        )
        .unwrap();
    let due = first
        .take_due_intent_order(u64::MAX)
        .unwrap()
        .expect("one child is due");
    let order_id = due.request.order_id.clone();
    let action_id = due.action_id.clone();
    first.submit(due.request).unwrap();
    assert_eq!(
        first.algorithm_runs()[0].actions[0].status,
        AlgorithmActionStatus::Pending
    );
    drop(first);

    let restored = application(&path);
    let run = restored.algorithm_runs().pop().unwrap();
    assert_eq!(run.actions.len(), 1);
    assert_eq!(run.actions[0].action_id, action_id);
    assert_eq!(run.actions[0].status, AlgorithmActionStatus::Completed);
    assert_eq!(run.status, AlgorithmRunStatus::Running);
    assert_eq!(restored.intents()[0].order_ids, vec![order_id.clone()]);
    assert_eq!(
        restored.orders(None)[0].leg_id,
        Some(run.legs[0].leg_id.clone())
    );

    let restored_again = application(&path);
    assert_eq!(restored_again.algorithm_runs(), vec![run]);
    assert_eq!(restored_again.intents()[0].order_ids, vec![order_id]);
}

#[test]
fn indeterminate_immediate_action_enters_reconciliation_and_restores() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(FailingOrderEntry::indeterminate())),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    configure_test_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());

    let result = app.submit_intent(strategy_intent("intent:indeterminate-immediate", 1, None));
    assert!(matches!(result, Err(ExecutionError::Indeterminate(_))));
    assert_eq!(
        app.intent("intent:indeterminate-immediate").unwrap().status,
        kairos_execution::IntentStatus::ReconciliationRequired
    );
    let run = app.algorithm_runs().pop().unwrap();
    assert_eq!(run.status, AlgorithmRunStatus::ReconciliationRequired);
    assert_eq!(run.actions.len(), 1);
    assert_eq!(run.actions[0].status, AlgorithmActionStatus::Indeterminate);

    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(restored.algorithm_runs(), vec![run]);
}

#[test]
fn rejected_strategy_intent_is_durable_and_idempotent() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let mut intent = strategy_intent("intent:rejected", 1, None);
    intent.min_edge_bps = Some(1_000_001);

    let first = app
        .submit_intent_with_idempotency(intent.clone(), "rejected-command".into())
        .unwrap_err();
    assert!(first.to_string().contains("out of range"));
    let state = app
        .intent("intent:rejected")
        .expect("rejected state is durable");
    assert_eq!(state.status, kairos_execution::IntentStatus::Rejected);
    assert_eq!(
        state.intent.strategy_decision_id.as_deref(),
        Some("decision:intent:rejected")
    );
    let events = app.intent_events(Some("intent:rejected"));
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].previous_status, None);
    assert_eq!(events[0].status, kairos_execution::IntentStatus::Rejected);

    let replay = app
        .submit_intent_with_idempotency(intent, "rejected-command".into())
        .unwrap_err();
    assert_eq!(replay.to_string(), first.to_string());
    assert_eq!(app.intent_events(Some("intent:rejected")).len(), 1);

    let restored = application(&path);
    assert_eq!(
        restored
            .intent("intent:rejected")
            .expect("rejected state restores")
            .status,
        kairos_execution::IntentStatus::Rejected
    );
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

fn option_spread_intent(short_quantity: i64, long_quantity: i64) -> ExecuteStrategyIntent {
    let mut intent = strategy_intent("intent:option-spread", 0, None);
    intent.intent_type = kairos_execution::IntentType::OptionSpread;
    intent.completion_policy = kairos_execution::CompletionPolicy::AllOrNothing;
    intent.failure_policy = kairos_execution::FailurePolicy::CancelRemaining;
    intent.minimum_net_credit = Some(Money::new(120, 2).unwrap());
    intent.maximum_loss = Some(Money::new(880, 0).unwrap());
    intent.legs = vec![
        intent_leg(
            "short-put",
            "main",
            "options",
            "SPY-P-500",
            Some("spy-options"),
            OrderSide::Sell,
            short_quantity,
            None,
        ),
        intent_leg(
            "long-put",
            "main",
            "options",
            "SPY-P-490",
            Some("spy-options"),
            OrderSide::Buy,
            long_quantity,
            None,
        ),
    ];
    intent
}

#[test]
fn option_spread_intent_requires_fixed_risk_package_shape() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app.submit_intent(option_spread_intent(1, 1)).unwrap();
    assert_eq!(
        state.intent.intent_type,
        kairos_execution::IntentType::OptionSpread
    );
    assert_eq!(state.plan.unwrap().legs.len(), 2);
}

#[test]
fn option_spread_intent_rejects_unequal_leg_quantities() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let error = app.submit_intent(option_spread_intent(2, 1)).unwrap_err();
    assert!(error.to_string().contains("equal quantity"));
}

#[test]
fn option_spread_intent_rejects_missing_fixed_risk_limits() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let mut intent = option_spread_intent(1, 1);
    intent.maximum_loss = None;
    let error = app.submit_intent(intent).unwrap_err();
    assert!(error.to_string().contains("maximum loss"));
}

fn maker_taker_pair_intent(
    intent_id: &str,
    leader_quantity: i64,
    ratio_numerator: u64,
    max_unhedged_quantity: i64,
) -> ExecuteStrategyIntent {
    let mut intent = strategy_intent(intent_id, 0, None);
    intent.intent_type = kairos_execution::IntentType::PairArbitrage;
    intent.completion_policy = kairos_execution::CompletionPolicy::HedgeWithinTolerance;
    intent.failure_policy = kairos_execution::FailurePolicy::Compensate;
    intent.algorithm = kairos_execution::ExecutionAlgorithmPolicy::MakerTakerHedge(HedgePolicy {
        leader_leg_id: LegId::new("leader").unwrap(),
        hedge_leg_id: LegId::new("hedge").unwrap(),
        ratio: kairos_primitives::decimal::Ratio::new(ratio_numerator, 1).unwrap(),
        contract_multiplier: kairos_primitives::decimal::Ratio::new(1, 1).unwrap(),
        max_unhedged_quantity: Quantity::new(max_unhedged_quantity, 0).unwrap(),
        max_unhedged_duration: None,
        fallback_execution_route_ids: Vec::new(),
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
            leader_quantity,
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
}

fn add_fallback_route(intent: &mut ExecuteStrategyIntent) {
    let kairos_execution::ExecutionAlgorithmPolicy::MakerTakerHedge(policy) = &mut intent.algorithm
    else {
        panic!("maker-taker fixture has a hedge policy");
    };
    policy.fallback_execution_route_ids =
        vec![ExecutionRouteId::new("execution-route:fallback").unwrap()];
}

#[test]
fn twap_uses_persisted_business_deadlines_and_restores_the_next_slice() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let mut intent = strategy_intent("intent:twap-schedule", 6, Some(100));
    intent.source_event_time_unix_nanos = Some(UnixNanos::new(100));
    intent.algorithm =
        kairos_execution::ExecutionAlgorithmPolicy::Twap(kairos_execution::TwapPolicy {
            slice_count: 3,
            slice_interval: DurationNanos::new(10),
        });

    let state = app.submit_intent(intent).unwrap();
    let run = app.algorithm_runs().pop().unwrap();
    assert!(matches!(
        run.spec,
        kairos_execution::ExecutionAlgorithmSpec::Twap(_)
    ));
    assert_eq!(run.next_wake_at, Some(UnixNanos::new(110)));
    assert_eq!(app.orders(None).len(), 1);
    assert_eq!(state.pending_orders.len(), 2);
    assert_eq!(
        run.actions
            .iter()
            .filter(|action| matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::TwapSlice,
                    ..
                }
            ))
            .count(),
        1
    );

    assert_eq!(app.advance_due_algorithm_runs(109, 8).unwrap(), 0);
    assert_eq!(app.orders(None).len(), 1);
    assert_eq!(app.advance_due_algorithm_runs(110, 8).unwrap(), 1);
    assert_eq!(app.orders(None).len(), 2);
    assert_eq!(
        app.algorithm_runs()[0].next_wake_at,
        Some(UnixNanos::new(120))
    );
    drop(app);

    let mut restored = application(&path);
    assert_eq!(
        restored.algorithm_runs()[0].next_wake_at,
        Some(UnixNanos::new(120))
    );
    assert_eq!(
        restored
            .intent("intent:twap-schedule")
            .unwrap()
            .pending_orders
            .len(),
        1
    );
    assert_eq!(restored.advance_due_algorithm_runs(119, 8).unwrap(), 0);
    assert_eq!(restored.advance_due_algorithm_runs(120, 8).unwrap(), 1);
    let orders = restored.orders(None);
    assert_eq!(orders.len(), 3);
    assert_eq!(
        orders
            .iter()
            .map(|order| order.quantity)
            .collect::<Vec<_>>(),
        vec![
            Quantity::new(2, 0).unwrap(),
            Quantity::new(2, 0).unwrap(),
            Quantity::new(2, 0).unwrap(),
        ]
    );
    assert_eq!(
        restored.algorithm_runs()[0]
            .actions
            .iter()
            .filter(|action| matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::TwapSlice,
                    ..
                }
            ))
            .count(),
        3
    );

    for (index, order) in orders.into_iter().enumerate() {
        restored
            .record_fill(fill_report(
                format!("twap-fill-{index}"),
                order.order_id.to_string(),
                2,
                100,
                0,
                Some(121 + index as u64),
            ))
            .unwrap();
    }
    assert_eq!(
        restored.algorithm_runs()[0].status,
        AlgorithmRunStatus::Completed
    );
    assert_eq!(
        restored.intent("intent:twap-schedule").unwrap().status,
        kairos_execution::IntentStatus::Satisfied
    );
}

#[test]
fn twap_rejects_a_second_split_quantity_path() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let mut intent = strategy_intent("intent:twap-with-split", 6, Some(100));
    intent.algorithm =
        kairos_execution::ExecutionAlgorithmPolicy::Twap(kairos_execution::TwapPolicy {
            slice_count: 3,
            slice_interval: DurationNanos::new(10),
        });
    intent.order_options.split = Some(SplitOrderPolicy {
        max_child_quantity: None,
        child_count: Some(2),
        min_child_quantity: None,
    });

    let error = app.submit_intent(intent).unwrap_err();
    assert!(matches!(
        error,
        ExecutionError::Invalid(message)
            if message == "TWAP owns slice quantity and cannot be combined with split order options"
    ));
}

#[test]
fn shared_algorithm_preparation_routes_twap_to_the_due_order_path() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let mut intent = strategy_intent("intent:twap-shared-decision", 4, Some(100));
    intent.source_event_time_unix_nanos = Some(UnixNanos::new(100));
    intent.algorithm =
        kairos_execution::ExecutionAlgorithmPolicy::Twap(kairos_execution::TwapPolicy {
            slice_count: 2,
            slice_interval: DurationNanos::new(10),
        });

    app.submit_intent(intent).unwrap();
    let due = app.prepare_due_algorithm_runs(110, 8).unwrap();
    assert_eq!(due, vec!["intent:twap-shared-decision"]);
    let run = &app.algorithm_runs()[0];
    assert_eq!(run.next_wake_at, Some(UnixNanos::new(110)));
    assert_eq!(
        run.actions
            .iter()
            .filter(|action| matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::TwapSlice,
                    ..
                }
            ))
            .count(),
        1
    );
    assert_eq!(app.orders(None).len(), 1);

    app.advance_due_intent_orders(110, 8).unwrap();
    assert_eq!(app.orders(None).len(), 2);
    let run = &app.algorithm_runs()[0];
    assert_eq!(run.next_wake_at, None);
    assert_eq!(
        run.actions
            .iter()
            .filter(|action| matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::TwapSlice,
                    ..
                }
            ))
            .count(),
        2
    );
}

#[tokio::test(flavor = "current_thread")]
async fn conflux_managed_runtime_dispatches_due_twap_through_its_venue_connection() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener
        .set_nonblocking(false)
        .expect("test listener accepts blocking connections");
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let server = std::thread::spawn(move || {
        loop {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut buffer = [0_u8; 16 * 1024];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            let request_line = request.lines().next().unwrap_or_default();
            assert!(
                request_line.contains("/api/v3/time") || request_line.contains("/api/v3/order"),
                "unexpected Binance request: {request_line}"
            );
            let submitted = request_line.contains("/api/v3/order");
            let body = if submitted {
                r#"{"orderId":42}"#.to_owned()
            } else {
                let now_millis = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_millis();
                format!(r#"{{"serverTime":{now_millis}}}"#)
            };
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
            if submitted {
                break;
            }
        }
    });

    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("execution.json");
    let mut application = application(&state_path);
    let identity = kairos_primitives::runtime::InstanceIdentity::new(
        "workspace:test",
        "launch:test",
        "instance:test",
    )
    .unwrap();
    application
        .configure_conflux(
            Vec::new(),
            Vec::new(),
            identity.clone(),
            ExecutionAudit::from(MemoryExecutionAudit::new(Vec::new())),
            None,
        )
        .unwrap();
    let entry_key = ConnectionKey::new("execution.test.spot.command").unwrap();
    let mut system = ConfluxSystem::new();
    system
        .connections()
        .binance_spot_rest
        .create(
            entry_key.clone(),
            kairos_conflux::BinanceRestConfig {
                environment: "test".into(),
                endpoint,
                credential: Some(kairos_conflux::BinanceCredential {
                    principal_id: "test".into(),
                    api_key: SecretString::from("test-api-key".to_owned()),
                    secret: SecretString::from("test-secret".to_owned()),
                }),
            },
        )
        .unwrap();
    for kind in [
        ExecutionViewKind::ActiveOrders,
        ExecutionViewKind::CurrentExecution,
        ExecutionViewKind::ActiveIntents,
    ] {
        let key = ExecutionViewKey::from_identity(&identity, kind);
        system
            .outputs()
            .mmap
            .declare(
                key.canonical_key(),
                MmapOutputDeclaration {
                    path: ExecutionViewPublisher::resolved_path(directory.path(), &key).unwrap(),
                    slot_capacity: 1024 * 1024,
                    revision: 1,
                },
            )
            .unwrap();
    }
    let (conflux, handle) = Conflux::new(application, system, ConfluxConfig::default()).unwrap();

    tokio::task::LocalSet::new()
        .run_until(async move {
            let process = tokio::task::spawn_local(conflux.run());
            let invocation = handle
                .rpc_actor_invocation(Duration::from_secs(5))
                .call(move |application, context| {
                    Box::pin(async move {
                        application
                            .configure_conflux(
                                vec![ExecutionConnectionPlan {
                                    route_id: "test".into(),
                                    required: true,
                                    account_id: AccountId::new("main").unwrap(),
                                    segment_key: SegmentKey::new("spot").unwrap(),
                                    instrument_type: ParticipantInstrumentTypeRef::new("spot")
                                        .unwrap(),
                                    entry_key: entry_key.to_string(),
                                    query_key: "execution.test.spot.query".into(),
                                    stream_key: "execution.test.spot.stream".into(),
                                }],
                                Vec::new(),
                                identity,
                                ExecutionAudit::from(MemoryExecutionAudit::new(Vec::new())),
                                None,
                            )
                            .unwrap();
                        let mut intent =
                            strategy_intent("intent:twap-conflux-managed", 4, Some(100));
                        intent.source_event_time_unix_nanos = Some(UnixNanos::new(100));
                        intent.algorithm = kairos_execution::ExecutionAlgorithmPolicy::Twap(
                            kairos_execution::TwapPolicy {
                                slice_count: 2,
                                slice_interval: DurationNanos::new(10),
                            },
                        );
                        application.submit_intent(intent).unwrap();
                        application
                            .advance_due_algorithm_runs_managed(110, 8, context)
                            .await
                            .unwrap();
                        assert_eq!(application.orders(None).len(), 2);
                        assert_eq!(
                            application.algorithm_runs()[0]
                                .actions
                                .iter()
                                .filter(|action| matches!(
                                    action.kind,
                                    AlgorithmActionKind::SubmitChild {
                                        execution_style: AlgorithmExecutionStyle::TwapSlice,
                                        ..
                                    }
                                ))
                                .count(),
                            2
                        );
                        assert!(
                            application
                                .orders(None)
                                .iter()
                                .any(|order| order.remote_order_id.as_deref() == Some("42"))
                        );
                        while application.pending_business_event().is_some() {
                            application.acknowledge_business_event();
                        }
                        Ok(())
                    })
                })
                .await;
            if let Err(error) = invocation {
                match process.await.unwrap() {
                    Ok(_) => panic!("managed invocation failed: {error}; actor exited cleanly"),
                    Err(actor_error) => {
                        panic!("managed invocation failed: {error}; actor failed: {actor_error}")
                    },
                }
            }
            handle.shutdown(ShutdownMode::Drain);
            process.await.unwrap().unwrap();
        })
        .await;
    server.join().unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn managed_twap_response_loss_reconciles_by_query_after_restart() {
    let submit_listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let submit_endpoint = format!("http://{}", submit_listener.local_addr().unwrap());
    let submit_server = std::thread::spawn(move || loop {
        let (mut stream, _) = submit_listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut buffer = [0_u8; 16 * 1024];
        let size = stream.read(&mut buffer).unwrap();
        let request = String::from_utf8_lossy(&buffer[..size]);
        let request_line = request.lines().next().unwrap_or_default();
        let submitted = request_line.contains("/api/v3/order");
        assert!(
            submitted || request_line.contains("/api/v3/time"),
            "unexpected Binance submit request: {request_line}"
        );
        let body = if submitted {
            // The venue accepted the order but its acknowledgement lost the
            // required identity. Execution must reconcile instead of retrying.
            "{}".to_owned()
        } else {
            let now_millis = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis();
            format!(r#"{{"serverTime":{now_millis}}}"#)
        };
        write!(
            stream,
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
        if submitted {
            break;
        }
    });

    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("execution.json");
    let identity = kairos_primitives::runtime::InstanceIdentity::new(
        "workspace:test",
        "launch:test",
        "instance:restart",
    )
    .unwrap();
    let entry_key = ConnectionKey::new("execution.test.spot.command").unwrap();
    let plan = ExecutionConnectionPlan {
        route_id: "test".into(),
        required: true,
        account_id: AccountId::new("main").unwrap(),
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_type: ParticipantInstrumentTypeRef::new("spot").unwrap(),
        entry_key: entry_key.to_string(),
        query_key: entry_key.to_string(),
        stream_key: "execution.test.spot.stream".into(),
    };
    let mut first = application(&state_path);
    first
        .configure_conflux(
            Vec::new(),
            Vec::new(),
            identity.clone(),
            ExecutionAudit::from(MemoryExecutionAudit::new(Vec::new())),
            None,
        )
        .unwrap();
    let mut first_system = ConfluxSystem::new();
    first_system
        .connections()
        .binance_spot_rest
        .create(
            entry_key.clone(),
            kairos_conflux::BinanceRestConfig {
                environment: "test".into(),
                endpoint: submit_endpoint,
                credential: Some(kairos_conflux::BinanceCredential {
                    principal_id: "test".into(),
                    api_key: SecretString::from("test-api-key".to_owned()),
                    secret: SecretString::from("test-secret".to_owned()),
                }),
            },
        )
        .unwrap();
    for kind in [
        ExecutionViewKind::ActiveOrders,
        ExecutionViewKind::CurrentExecution,
        ExecutionViewKind::ActiveIntents,
    ] {
        let key = ExecutionViewKey::from_identity(&identity, kind);
        first_system
            .outputs()
            .mmap
            .declare(
                key.canonical_key(),
                MmapOutputDeclaration {
                    path: ExecutionViewPublisher::resolved_path(directory.path(), &key).unwrap(),
                    slot_capacity: 1024 * 1024,
                    revision: 1,
                },
            )
            .unwrap();
    }
    let (first_conflux, first_handle) =
        Conflux::new(first, first_system, ConfluxConfig::default()).unwrap();
    let first_actor = tokio::task::LocalSet::new()
        .run_until(async move {
            let process = tokio::task::spawn_local(first_conflux.run());
            first_handle
                .rpc_actor_invocation(Duration::from_secs(5))
                .call(move |application, context| {
                    Box::pin(async move {
                        application
                            .configure_conflux(
                                vec![plan],
                                Vec::new(),
                                identity,
                                ExecutionAudit::from(MemoryExecutionAudit::new(Vec::new())),
                                None,
                            )
                            .unwrap();
                        let mut intent =
                            strategy_intent("intent:twap-managed-restart", 4, Some(100));
                        intent.source_event_time_unix_nanos = Some(UnixNanos::new(100));
                        intent.algorithm = kairos_execution::ExecutionAlgorithmPolicy::Twap(
                            kairos_execution::TwapPolicy {
                                slice_count: 2,
                                slice_interval: DurationNanos::new(10),
                            },
                        );
                        application.submit_intent(intent).unwrap();
                        let error = application
                            .advance_due_algorithm_runs_managed(110, 8, context)
                            .await
                            .unwrap_err();
                        assert!(matches!(error, ExecutionError::Indeterminate(_)));
                        assert_eq!(application.orders(None).len(), 2);
                        assert_eq!(
                            application.algorithm_runs()[0].actions[1].status,
                            AlgorithmActionStatus::Indeterminate
                        );
                        while application.pending_business_event().is_some() {
                            application.acknowledge_business_event();
                        }
                        Ok(())
                    })
                })
                .await
                .unwrap();
            first_handle.shutdown(ShutdownMode::Drain);
            process.await.unwrap().unwrap().actor
        })
        .await;
    submit_server.join().unwrap();
    let uncertain_order_id = first_actor
        .orders(None)
        .into_iter()
        .find(|order| order.status == ExecutionOrderStatus::Unknown)
        .expect("response loss leaves one uncertain order")
        .order_id
        .to_string();

    let query_listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let query_endpoint = format!("http://{}", query_listener.local_addr().unwrap());
    let queried_order_id = uncertain_order_id.clone();
    let query_server = std::thread::spawn(move || {
        let mut observed_open = false;
        let mut observed_history = false;
        while !observed_open || !observed_history {
            let (mut stream, _) = query_listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut buffer = [0_u8; 16 * 1024];
            let size = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..size]);
            let request_line = request.lines().next().unwrap_or_default();
            let body = if request_line.contains("/api/v3/time") {
                let now_millis = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_millis();
                format!(r#"{{"serverTime":{now_millis}}}"#)
            } else if request_line.contains("/api/v3/openOrders") {
                observed_open = true;
                serde_json::json!([{
                    "orderId": 99,
                    "clientOrderId": queried_order_id,
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "status": "NEW",
                    "origQty": "2",
                    "executedQty": "0",
                    "price": "100",
                    "updateTime": 1_700_000_000_000_u64
                }])
                .to_string()
            } else if request_line.contains("/api/v3/allOrders") {
                observed_history = true;
                serde_json::json!([{
                    "orderId": 99,
                    "clientOrderId": queried_order_id,
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "type": "LIMIT",
                    "status": "NEW",
                    "origQty": "2",
                    "executedQty": "0",
                    "price": "100",
                    "updateTime": 1_700_000_000_000_u64
                }])
                .to_string()
            } else {
                panic!("unexpected Binance query request: {request_line}");
            };
            write!(
                stream,
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        }
    });

    let mut restored = application(&state_path);
    assert_eq!(restored.orders(None).len(), 2);
    assert_eq!(
        restored.algorithm_runs()[0].actions[1].status,
        AlgorithmActionStatus::Indeterminate
    );
    restored
        .configure_conflux(
            Vec::new(),
            Vec::new(),
            identity.clone(),
            ExecutionAudit::from(MemoryExecutionAudit::new(Vec::new())),
            None,
        )
        .unwrap();
    let mut restored_system = ConfluxSystem::new();
    restored_system
        .connections()
        .binance_spot_rest
        .create(
            entry_key.clone(),
            kairos_conflux::BinanceRestConfig {
                environment: "test".into(),
                endpoint: query_endpoint,
                credential: Some(kairos_conflux::BinanceCredential {
                    principal_id: "test".into(),
                    api_key: SecretString::from("test-api-key".to_owned()),
                    secret: SecretString::from("test-secret".to_owned()),
                }),
            },
        )
        .unwrap();
    for kind in [
        ExecutionViewKind::ActiveOrders,
        ExecutionViewKind::CurrentExecution,
        ExecutionViewKind::ActiveIntents,
    ] {
        let key = ExecutionViewKey::from_identity(&identity, kind);
        restored_system
            .outputs()
            .mmap
            .declare(
                key.canonical_key(),
                MmapOutputDeclaration {
                    path: ExecutionViewPublisher::resolved_path(directory.path(), &key).unwrap(),
                    slot_capacity: 1024 * 1024,
                    revision: 1,
                },
            )
            .unwrap();
    }
    let (restored_conflux, restored_handle) =
        Conflux::new(restored, restored_system, ConfluxConfig::default()).unwrap();
    let restored_plan = ExecutionConnectionPlan {
        route_id: "test".into(),
        required: true,
        account_id: AccountId::new("main").unwrap(),
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_type: ParticipantInstrumentTypeRef::new("spot").unwrap(),
        entry_key: entry_key.to_string(),
        query_key: entry_key.to_string(),
        stream_key: "execution.test.spot.stream".into(),
    };
    let final_actor = tokio::task::LocalSet::new()
        .run_until(async move {
            let process = tokio::task::spawn_local(restored_conflux.run());
            restored_handle
                .rpc_actor_invocation(Duration::from_secs(5))
                .call(move |application, context| {
                    Box::pin(async move {
                        application
                            .configure_conflux(
                                vec![restored_plan],
                                Vec::new(),
                                identity,
                                ExecutionAudit::from(MemoryExecutionAudit::new(Vec::new())),
                                None,
                            )
                            .unwrap();
                        let changed = application
                            .reconcile_managed_orders(Default::default(), context)
                            .await
                            .unwrap();
                        assert_eq!(changed, 1);
                        assert_eq!(application.orders(None).len(), 2);
                        let reconciled = application
                            .orders(None)
                            .into_iter()
                            .find(|order| order.order_id.as_str() == uncertain_order_id)
                            .unwrap();
                        assert_eq!(reconciled.status, ExecutionOrderStatus::Accepted);
                        assert_eq!(reconciled.remote_order_id.as_deref(), Some("99"));
                        assert_ne!(
                            application.algorithm_runs()[0].actions[1].status,
                            AlgorithmActionStatus::Indeterminate
                        );
                        while application.pending_business_event().is_some() {
                            application.acknowledge_business_event();
                        }
                        Ok(())
                    })
                })
                .await
                .unwrap();
            restored_handle.shutdown(ShutdownMode::Drain);
            process.await.unwrap().unwrap().actor
        })
        .await;
    query_server.join().unwrap();
    assert_eq!(final_actor.orders(None).len(), 2);
    let persisted = application(&state_path);
    assert_eq!(persisted.orders(None).len(), 2);
    assert_eq!(
        persisted
            .orders(None)
            .into_iter()
            .find(|order| order.order_id.as_str() == uncertain_order_id)
            .unwrap()
            .remote_order_id
            .as_deref(),
        Some("99")
    );
}

#[tokio::test(flavor = "current_thread")]
#[ignore = "cross-language certification: requires uv and the Kairospy workspace"]
async fn kairospy_explicit_algorithm_round_trips_through_execution_json_rpc() {
    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("execution.json");
    let socket_path = directory.path().join("execution.sock");
    let mut application = application(&state_path);
    let mut intent = strategy_intent("intent:kairospy-rpc-immediate", 2, None);
    intent.strategy_decision_id = Some("decision:kairospy-rpc-immediate".into());
    intent.strategy_id = "python-strategy".into();
    intent.reason = "cross-language contract certification".into();
    application
        .submit_intent_with_idempotency(intent, "request:kairospy-rpc-immediate".into())
        .unwrap();
    while application.pending_business_event().is_some() {
        application.acknowledge_business_event();
    }
    let durable = application.pending_outbox(1_024).unwrap();
    application
        .acknowledge_outbox(&durable.iter().map(|entry| entry.id).collect::<Vec<_>>())
        .unwrap();

    let identity =
        kairos_primitives::runtime::InstanceIdentity::new("workspace:test", "launch", "instance")
            .unwrap();
    application
        .configure_conflux(
            Vec::new(),
            Vec::new(),
            identity.clone(),
            ExecutionAudit::from(MemoryExecutionAudit::new(Vec::new())),
            None,
        )
        .unwrap();
    let mut system = ConfluxSystem::new();
    for kind in [
        ExecutionViewKind::ActiveOrders,
        ExecutionViewKind::CurrentExecution,
        ExecutionViewKind::ActiveIntents,
    ] {
        let key = ExecutionViewKey::from_identity(&identity, kind);
        system
            .outputs()
            .mmap
            .declare(
                key.canonical_key(),
                MmapOutputDeclaration {
                    path: ExecutionViewPublisher::resolved_path(directory.path(), &key).unwrap(),
                    slot_capacity: 1024 * 1024,
                    revision: 1,
                },
            )
            .unwrap();
    }
    let (conflux, handle) = Conflux::new(application, system, ConfluxConfig::default()).unwrap();
    let methods = crate::application::ExecutionRpcService::<ExecutionApplication>::new(
        handle.rpc_actor_invocation(Duration::from_secs(5)),
    )
    .into_rpc();
    let runtime = conflux.with_json_rpc(
        handle.clone(),
        methods,
        JsonRpcRuntimeConfig::uds(socket_path.clone()),
    );

    tokio::task::LocalSet::new()
        .run_until(async move {
            let process = tokio::task::spawn_local(runtime.run());
            let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
            while (!socket_path.exists()
                || !matches!(handle.phase(), kairos_conflux::ProcessPhase::Running))
                && tokio::time::Instant::now() < deadline
            {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            assert!(
                socket_path.exists(),
                "Execution JSON-RPC socket did not start"
            );

            let script = r#"
import copy
from decimal import Decimal
import json
import sys

from kairospy.infrastructure.contracts.execution import ExecutionControlClient
from kairospy.infrastructure.transport.json_rpc import UnixJsonRpcClient
from kairospy.investment.apps.execution.application.commands import ExecutionCommandClient
from kairospy.strategy import ImmediateAlgorithm, TargetPositionRequest

class Capture:
    def __init__(self, delegate):
        self.delegate = delegate
        self.params = None

    def call(self, method, params=None):
        self.params = copy.deepcopy(params)
        return self.delegate.call(method, params)

socket_path = sys.argv[1]
control = ExecutionControlClient(socket_path, timeout=5)
capture = Capture(control)
execution = ExecutionCommandClient(capture, launch_id="launch")
result = execution.target_position(
    TargetPositionRequest(
        "BTCUSDT",
        Decimal("2"),
        algorithm=ImmediateAlgorithm(),
        account_id="main",
        execution_route_id="execution-route:test",
        intent_id="intent:kairospy-rpc-immediate",
        strategy_decision_id="decision:kairospy-rpc-immediate",
        reason="cross-language contract certification",
    ),
    strategy_id="python-strategy",
    instance_id="instance",
    request_id="request:kairospy-rpc-immediate",
)
assert result.status == "accepted", result
assert result.result["status"] == "duplicate", result
assert capture.params is not None
legacy = copy.deepcopy(capture.params[0])
legacy["command_id"] = "request:kairospy-rpc-legacy"
legacy["idempotency_key"] = "request:kairospy-rpc-legacy"
legacy["intent"]["hedge_policy"] = {}
legacy_rejected = False
legacy_error = ""
try:
    control.call("execution_submit_intent", [legacy])
except RuntimeError as error:
    legacy_error = str(error)
    legacy_rejected = "hedge_policy" in legacy_error or "Invalid params" in legacy_error
assert legacy_rejected, legacy_error
health = dict(control.health())
UnixJsonRpcClient(socket_path, timeout=5).call(
    "system_stop",
    [{"immediate": False, "reason": "cross-language certification complete"}],
)
print(json.dumps({
    "status": result.result["status"],
    "intent_id": result.result["intent_id"],
    "legacy_rejected": legacy_rejected,
    "health": health["status"],
}))
"#;
            let python_socket = socket_path.clone();
            let output = tokio::task::spawn_blocking(move || {
                std::process::Command::new("uv")
                    .args(["run", "python", "-c", script])
                    .arg(python_socket)
                    .current_dir(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.."))
                    .output()
            })
            .await
            .unwrap()
            .unwrap();
            assert!(
                output.status.success(),
                "Kairospy client failed:\nstdout={}\nstderr={}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
            let evidence: serde_json::Value =
                serde_json::from_slice(&output.stdout).expect("Kairospy emits JSON evidence");
            assert_eq!(evidence["status"], "duplicate");
            assert_eq!(evidence["intent_id"], "intent:kairospy-rpc-immediate");
            assert_eq!(evidence["legacy_rejected"], true);
            assert_eq!(evidence["health"], "ready");

            let outcome = process.await.unwrap().unwrap();
            assert_eq!(outcome.actor.intents().len(), 1);
            assert_eq!(outcome.actor.orders(None).len(), 1);
            assert!(matches!(
                outcome.actor.algorithm_runs()[0].spec,
                kairos_execution::ExecutionAlgorithmSpec::Immediate
            ));
        })
        .await;
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
            intent.algorithm =
                kairos_execution::ExecutionAlgorithmPolicy::MakerTakerHedge(HedgePolicy {
                    leader_leg_id: LegId::new("leader").unwrap(),
                    hedge_leg_id: LegId::new("hedge").unwrap(),
                    ratio: kairos_primitives::decimal::Ratio::new(2, 1).unwrap(),
                    contract_multiplier: kairos_primitives::decimal::Ratio::new(1, 1).unwrap(),
                    max_unhedged_quantity: Quantity::new(0, 0).unwrap(),
                    max_unhedged_duration: None,
                    fallback_execution_route_ids: Vec::new(),
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
    let hedge_plan = state
        .plan
        .as_ref()
        .unwrap()
        .legs
        .iter()
        .find(|leg| leg.leg_id == "hedge")
        .unwrap();
    assert!(hedge_plan.order_ids.is_empty());
    assert_eq!(hedge_plan.target_quantity, Quantity::new(8, 0).unwrap());
    assert_eq!(state.dormant_orders.len(), 1);
    assert_eq!(app.orders(None).len(), 1);
    assert!(app.orders(None)[0].selected_route.as_ref().is_some());
    app.record_fill(fill_report(
        "leader-fill",
        leader.to_string(),
        4,
        100,
        0,
        None,
    ))
    .unwrap();
    let hedge_order = app
        .orders(None)
        .into_iter()
        .find(|order| order.order_id.contains(":hedge:decision:"))
        .expect("leader fill creates a taker hedge");
    assert_eq!(hedge_order.quantity, Quantity::new(8, 0).unwrap());
    assert_eq!(hedge_order.side, OrderSide::Sell);
    assert_eq!(hedge_order.order_type, OrderType::Market);
    let run = app.algorithm_runs().pop().unwrap();
    assert_eq!(
        run.exposure.as_ref().unwrap().required_hedge_quantity,
        Quantity::new(8, 0).unwrap()
    );
    assert_eq!(
        run.exposure.as_ref().unwrap().hedge_committed_quantity,
        Quantity::new(8, 0).unwrap()
    );
    assert!(run.actions.iter().any(|action| matches!(
        action.kind,
        AlgorithmActionKind::SubmitChild {
            execution_style: AlgorithmExecutionStyle::TakerImmediate,
            ..
        }
    )));
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
fn maker_taker_threshold_and_restart_preserve_dormant_then_active_hedge() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut first = application(&path);
    let state = first
        .submit_intent(maker_taker_pair_intent(
            "intent:maker-taker-restart",
            4,
            1,
            1,
        ))
        .unwrap();
    let leader_order_id = state
        .plan
        .as_ref()
        .unwrap()
        .legs
        .iter()
        .find(|leg| leg.leg_id == "leader")
        .unwrap()
        .order_ids[0]
        .clone();
    assert_eq!(first.orders(None).len(), 1);
    assert_eq!(state.dormant_orders.len(), 1);
    drop(first);

    let mut restored = application(&path);
    assert_eq!(restored.orders(None).len(), 1);
    assert_eq!(restored.intents()[0].dormant_orders.len(), 1);
    assert_eq!(
        restored.algorithm_runs()[0].legs[1].lifecycle,
        kairos_execution::AlgorithmLegLifecycle::Dormant
    );

    restored
        .record_fill(fill_report(
            "maker-fill-below-threshold",
            leader_order_id.to_string(),
            1,
            100,
            0,
            Some(200),
        ))
        .unwrap();
    assert_eq!(restored.orders(None).len(), 1);
    restored
        .record_fill(fill_report(
            "maker-fill-above-threshold",
            leader_order_id.to_string(),
            1,
            100,
            0,
            Some(201),
        ))
        .unwrap();
    let hedge = restored
        .orders(None)
        .into_iter()
        .find(|order| order.order_id.contains(":hedge:decision:"))
        .unwrap();
    assert_eq!(hedge.quantity, Quantity::new(2, 0).unwrap());
    let run = restored.algorithm_runs().pop().unwrap();
    assert_eq!(
        run.exposure.as_ref().unwrap().unhedged_after_commitment,
        Quantity::ZERO
    );
    drop(restored);

    let restored = application(&path);
    assert_eq!(restored.algorithm_runs(), vec![run]);
    assert_eq!(restored.orders(None).len(), 2);
}

#[test]
fn maker_taker_tail_hedge_is_driven_by_persisted_business_time_deadline() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.advance_time(100).unwrap();
    let mut intent = maker_taker_pair_intent("intent:timed-tail-hedge", 1, 1, 1);
    intent.source_event_time_unix_nanos = Some(UnixNanos::new(100));
    let kairos_execution::ExecutionAlgorithmPolicy::MakerTakerHedge(policy) = &mut intent.algorithm
    else {
        panic!("maker-taker fixture has a hedge policy");
    };
    policy.max_unhedged_duration = Some(DurationNanos::new(10));
    let state = app.submit_intent(intent).unwrap();
    app.record_fill(fill_report(
        "timed-tail-leader-fill",
        state.order_ids[0].to_string(),
        1,
        100,
        0,
        Some(100),
    ))
    .unwrap();

    assert_eq!(app.orders(None).len(), 1);
    let waiting = app.algorithm_runs().pop().unwrap();
    assert_eq!(waiting.status, AlgorithmRunStatus::Waiting);
    assert_eq!(waiting.next_wake_at, Some(UnixNanos::new(110)));
    assert_eq!(
        waiting.exposure.as_ref().unwrap().unhedged_since,
        Some(UnixNanos::new(100))
    );
    drop(app);

    let mut app = application(&path);
    assert_eq!(app.algorithm_runs()[0], waiting);
    assert_eq!(app.advance_due_algorithm_runs(109, usize::MAX).unwrap(), 0);
    assert_eq!(app.orders(None).len(), 1);

    assert_eq!(app.advance_due_algorithm_runs(110, usize::MAX).unwrap(), 1);
    let hedge = app
        .orders(None)
        .into_iter()
        .find(|order| order.order_id.contains(":hedge:decision:"))
        .expect("business-time deadline must activate the tail hedge");
    assert_eq!(hedge.quantity, Quantity::new(1, 0).unwrap());
    assert_eq!(hedge.order_type, OrderType::Market);
    assert_eq!(app.algorithm_runs()[0].next_wake_at, None);
}

#[test]
fn maker_taker_acceptance_schedules_only_a_post_only_leader() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let (state, replayed) = app
        .accept_intent_with_idempotency_deferred(
            maker_taker_pair_intent("intent:maker-first-admission", 3, 1, 0),
            "maker-first-admission-command".into(),
        )
        .unwrap();

    assert!(!replayed);
    assert_eq!(state.pending_orders.len(), 1);
    assert_eq!(state.pending_orders[0].options.post_only, Some(true));
    assert_eq!(state.dormant_orders.len(), 1);
    assert_eq!(state.dormant_orders[0].side, OrderSide::Sell);
    assert!(
        state
            .plan
            .as_ref()
            .unwrap()
            .legs
            .iter()
            .find(|leg| leg.leg_id == "hedge")
            .unwrap()
            .order_ids
            .is_empty()
    );
    assert!(matches!(
        app.algorithm_runs()[0].spec,
        kairos_execution::ExecutionAlgorithmSpec::MakerTakerHedge(_)
    ));
}

#[test]
fn indeterminate_taker_hedge_blocks_reexecution_and_requires_reconciliation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(SecondSubmitIndeterminate { submissions: 0 })),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    configure_test_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());
    let state = app
        .submit_intent(maker_taker_pair_intent(
            "intent:indeterminate-hedge",
            2,
            1,
            0,
        ))
        .unwrap();
    let leader_order_id = state.order_ids[0].clone();

    app.record_fill(fill_report(
        "leader-fill-indeterminate-hedge",
        leader_order_id.to_string(),
        2,
        100,
        0,
        Some(300),
    ))
    .unwrap();
    assert_eq!(
        app.intent("intent:indeterminate-hedge").unwrap().status,
        kairos_execution::IntentStatus::ReconciliationRequired
    );
    let run = app.algorithm_runs().pop().unwrap();
    assert_eq!(run.status, AlgorithmRunStatus::ReconciliationRequired);
    assert_eq!(
        run.actions.last().unwrap().status,
        AlgorithmActionStatus::Indeterminate
    );
    assert_eq!(
        app.orders(None)
            .iter()
            .filter(|order| order.order_id.contains(":hedge:decision:"))
            .count(),
        1
    );

    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(restored.algorithm_runs(), vec![run]);
    assert_eq!(
        restored
            .orders(None)
            .iter()
            .filter(|order| order.order_id.contains(":hedge:decision:"))
            .count(),
        1
    );
}

#[test]
fn maker_taker_rejects_an_unconfigured_fallback_route_at_admission() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = application(&directory.path().join("execution.json"));
    let mut intent = maker_taker_pair_intent("intent:unknown-fallback", 2, 1, 0);
    add_fallback_route(&mut intent);

    let error = app.submit_intent(intent).unwrap_err();

    assert!(error.to_string().contains("is not configured"));
    assert!(app.intent("intent:unknown-fallback").is_none());
}

#[test]
fn proven_primary_hedge_failure_uses_the_configured_fallback_and_restores() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(PrimaryHedgeFailsThenFallback {
            submissions: 0,
            fallback_outcome: FallbackOutcome::Confirmed,
        })),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    configure_test_access(&mut app);
    configure_fallback_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());
    let mut intent = maker_taker_pair_intent("intent:fallback-hedge", 2, 1, 0);
    add_fallback_route(&mut intent);
    let state = app.submit_intent(intent).unwrap();

    app.record_fill(fill_report(
        "leader-fill-before-fallback",
        state.order_ids[0].to_string(),
        2,
        100,
        0,
        Some(480),
    ))
    .unwrap();

    let run = app.algorithm_runs().pop().unwrap();
    let taker_routes = run
        .actions
        .iter()
        .filter_map(|action| match &action.kind {
            AlgorithmActionKind::SubmitChild {
                execution_style: AlgorithmExecutionStyle::TakerImmediate,
                execution_route_id,
                ..
            } => execution_route_id.as_ref().map(ToString::to_string),
            _ => None,
        })
        .collect::<Vec<_>>();
    assert_eq!(
        taker_routes,
        vec!["execution-route:test", "execution-route:fallback"]
    );
    assert!(!run.actions.iter().any(|action| matches!(
        action.kind,
        AlgorithmActionKind::SubmitChild {
            execution_style: AlgorithmExecutionStyle::UnwindImmediate,
            ..
        }
    )));
    let fallback_order = app
        .orders(None)
        .into_iter()
        .find(|order| {
            order
                .execution_route_id
                .as_ref()
                .is_some_and(|route_id| route_id.as_str() == "execution-route:fallback")
        })
        .expect("fallback route must own the second hedge submission");
    assert_eq!(fallback_order.order_type, OrderType::Market);
    assert_eq!(fallback_order.status, ExecutionOrderStatus::Accepted);
    drop(app);

    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(restored.algorithm_runs(), vec![run]);
    assert_eq!(
        restored
            .orders(None)
            .iter()
            .filter(|order| order
                .execution_route_id
                .as_ref()
                .is_some_and(|route_id| { route_id.as_str() == "execution-route:fallback" }))
            .count(),
        1
    );
}

#[test]
fn staged_fallback_is_resumed_after_crash_with_the_same_route_and_action() {
    let shared = Arc::new(Mutex::new(None));
    let mut first = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(PrimaryHedgeFailsThenFallback {
            submissions: 0,
            fallback_outcome: FallbackOutcome::Confirmed,
        })),
        Some(Box::new(CrashAfterStagingFallbackStore {
            snapshot: Arc::clone(&shared),
            fail_once: true,
        })),
    )
    .unwrap();
    configure_test_access(&mut first);
    configure_fallback_access(&mut first);
    attach_simulated_risk(&mut first, test_risk());
    let mut intent = maker_taker_pair_intent("intent:staged-fallback-crash", 2, 1, 0);
    add_fallback_route(&mut intent);
    let state = first.submit_intent(intent).unwrap();

    let error = first
        .record_fill(fill_report(
            "leader-fill-before-staged-fallback",
            state.order_ids[0].to_string(),
            2,
            100,
            0,
            Some(485),
        ))
        .unwrap_err();
    assert!(error.to_string().contains("fallback action and request"));
    drop(first);

    let mut restored = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(PrimaryHedgeFailsThenFallback {
            submissions: 2,
            fallback_outcome: FallbackOutcome::Confirmed,
        })),
        Some(Box::new(SharedSnapshotStore {
            snapshot: Arc::clone(&shared),
        })),
    )
    .unwrap();
    configure_test_access(&mut restored);
    configure_fallback_access(&mut restored);
    attach_simulated_risk(&mut restored, test_risk());
    let before = restored.algorithm_runs().pop().unwrap();
    let pending = before.pending_actions().collect::<Vec<_>>();
    assert_eq!(pending.len(), 1);
    assert!(matches!(
        &pending[0].kind,
        AlgorithmActionKind::SubmitChild {
            execution_style: AlgorithmExecutionStyle::TakerImmediate,
            execution_route_id: Some(route_id),
            ..
        } if route_id.as_str() == "execution-route:fallback"
    ));

    assert_eq!(restored.advance_due_intent_orders(u64::MAX, 1).unwrap(), 1);
    let after = restored.algorithm_runs().pop().unwrap();
    assert_eq!(after.pending_actions().count(), 0);
    assert_eq!(before.actions.len(), after.actions.len());
    assert_eq!(
        restored
            .orders(None)
            .iter()
            .filter(|order| order
                .execution_route_id
                .as_ref()
                .is_some_and(|route_id| { route_id.as_str() == "execution-route:fallback" }))
            .count(),
        1
    );
}

#[test]
fn indeterminate_fallback_hedge_stops_without_unwind_or_another_route() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(PrimaryHedgeFailsThenFallback {
            submissions: 0,
            fallback_outcome: FallbackOutcome::Indeterminate,
        })),
        Some(Box::new(FileExecutionStore::new(
            directory.path().join("execution.json"),
        ))),
    )
    .unwrap();
    configure_test_access(&mut app);
    configure_fallback_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());
    let mut intent = maker_taker_pair_intent("intent:indeterminate-fallback", 2, 1, 0);
    add_fallback_route(&mut intent);
    let state = app.submit_intent(intent).unwrap();

    app.record_fill(fill_report(
        "leader-fill-before-indeterminate-fallback",
        state.order_ids[0].to_string(),
        2,
        100,
        0,
        Some(490),
    ))
    .unwrap();

    let run = app.algorithm_runs().pop().unwrap();
    assert_eq!(run.status, AlgorithmRunStatus::ReconciliationRequired);
    assert_eq!(
        app.intent("intent:indeterminate-fallback").unwrap().status,
        kairos_execution::IntentStatus::ReconciliationRequired
    );
    assert_eq!(
        run.actions
            .iter()
            .filter(|action| matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::TakerImmediate,
                    ..
                }
            ))
            .count(),
        2
    );
    assert!(!run.actions.iter().any(|action| matches!(
        action.kind,
        AlgorithmActionKind::SubmitChild {
            execution_style: AlgorithmExecutionStyle::UnwindImmediate,
            ..
        }
    )));
}

#[test]
fn exhausted_fallback_routes_unwind_only_after_every_known_failure() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(PrimaryHedgeFailsThenFallback {
            submissions: 0,
            fallback_outcome: FallbackOutcome::Failed,
        })),
        Some(Box::new(FileExecutionStore::new(
            directory.path().join("execution.json"),
        ))),
    )
    .unwrap();
    configure_test_access(&mut app);
    configure_fallback_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());
    let mut intent = maker_taker_pair_intent("intent:exhausted-fallback", 2, 1, 0);
    add_fallback_route(&mut intent);
    intent.max_slippage_bps = Some(100);
    let state = app.submit_intent(intent).unwrap();

    app.record_fill(fill_report(
        "leader-fill-before-exhausted-fallback",
        state.order_ids[0].to_string(),
        2,
        100,
        0,
        Some(495),
    ))
    .unwrap();

    let run = app.algorithm_runs().pop().unwrap();
    let styles = run
        .actions
        .iter()
        .filter_map(|action| match action.kind {
            AlgorithmActionKind::SubmitChild {
                execution_style, ..
            } => Some(execution_style),
            _ => None,
        })
        .collect::<Vec<_>>();
    assert_eq!(
        styles,
        vec![
            AlgorithmExecutionStyle::MakerPostOnly,
            AlgorithmExecutionStyle::TakerImmediate,
            AlgorithmExecutionStyle::TakerImmediate,
            AlgorithmExecutionStyle::UnwindImmediate,
        ]
    );
    assert!(
        app.orders(None)
            .iter()
            .any(|order| order.order_id.contains(":unwind:decision:"))
    );
}

#[test]
fn known_taker_hedge_failure_executes_price_protected_unwind_and_restores_terminal_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(HedgeFailsThenUnwindConfirms {
            submissions: 0,
            unwind_indeterminate: false,
        })),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    configure_test_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());
    let mut intent = maker_taker_pair_intent("intent:known-hedge-failure", 2, 1, 0);
    intent.max_slippage_bps = Some(100);
    let state = app.submit_intent(intent).unwrap();
    let leader_order_id = state.order_ids[0].clone();

    app.record_fill(fill_report(
        "leader-fill-before-unwind",
        leader_order_id.to_string(),
        2,
        100,
        0,
        Some(500),
    ))
    .unwrap();

    let unwind_order = app
        .orders(None)
        .into_iter()
        .find(|order| order.order_id.contains(":unwind:decision:"))
        .expect("a proven-not-sent hedge failure must submit the authorized unwind");
    assert_eq!(unwind_order.side, OrderSide::Sell);
    assert_eq!(unwind_order.order_type, OrderType::Limit);
    assert_eq!(unwind_order.quantity, Quantity::new(2, 0).unwrap());
    assert_eq!(unwind_order.limit_price, Some(Price::new(99, 0).unwrap()));
    assert_eq!(
        app.intent("intent:known-hedge-failure").unwrap().status,
        kairos_execution::IntentStatus::Compensating
    );
    let running = app.algorithm_runs().pop().unwrap();
    assert_eq!(running.status, AlgorithmRunStatus::Running);
    assert_eq!(
        running.exposure.as_ref().unwrap().unwind_committed_quantity,
        Quantity::new(2, 0).unwrap()
    );
    assert_eq!(
        running.exposure.as_ref().unwrap().unhedged_after_commitment,
        Quantity::ZERO
    );
    assert_eq!(
        running
            .actions
            .iter()
            .filter(|action| matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::UnwindImmediate,
                    ..
                }
            ))
            .count(),
        1
    );

    app.record_fill(fill_report(
        "emergency-unwind-fill",
        unwind_order.order_id.to_string(),
        2,
        99,
        0,
        Some(501),
    ))
    .unwrap();

    let terminal = app.algorithm_runs().pop().unwrap();
    assert_eq!(terminal.status, AlgorithmRunStatus::Unwound);
    let exposure = terminal.exposure.as_ref().unwrap();
    assert_eq!(
        exposure.leader_filled_quantity,
        Quantity::new(2, 0).unwrap()
    );
    assert_eq!(
        exposure.unwind_filled_quantity,
        Quantity::new(2, 0).unwrap()
    );
    assert_eq!(exposure.net_leader_filled_quantity, Quantity::ZERO);
    assert_eq!(exposure.required_hedge_quantity, Quantity::ZERO);
    let terminal_intent = app.intent("intent:known-hedge-failure").unwrap();
    assert_eq!(
        terminal_intent.status,
        kairos_execution::IntentStatus::Failed
    );
    assert_eq!(
        terminal_intent.completed_quantity,
        Quantity::new(2, 0).unwrap()
    );
    assert!(terminal_intent.reason.contains("emergency unwind"));
    assert_eq!(
        terminal_intent
            .plan
            .as_ref()
            .unwrap()
            .legs
            .iter()
            .find(|leg| leg.leg_id == "leader")
            .unwrap()
            .order_ids,
        vec![leader_order_id]
    );
    drop(app);

    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(restored.algorithm_runs(), vec![terminal]);
    assert_eq!(
        restored
            .orders(None)
            .iter()
            .filter(|order| order.order_id.contains(":unwind:decision:"))
            .count(),
        1
    );
    assert_eq!(
        restored
            .intent("intent:known-hedge-failure")
            .unwrap()
            .status,
        kairos_execution::IntentStatus::Failed
    );
}

#[test]
fn partial_unwind_terminal_event_submits_only_the_remaining_exposure() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(HedgeFailsThenUnwindConfirms {
            submissions: 0,
            unwind_indeterminate: false,
        })),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    configure_test_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());
    let mut intent = maker_taker_pair_intent("intent:partial-unwind", 2, 1, 0);
    intent.max_slippage_bps = Some(100);
    let state = app.submit_intent(intent).unwrap();
    app.record_fill(fill_report(
        "leader-fill-before-partial-unwind",
        state.order_ids[0].to_string(),
        2,
        100,
        0,
        Some(520),
    ))
    .unwrap();
    let first_unwind = app
        .orders(None)
        .into_iter()
        .find(|order| order.order_id.contains(":unwind:decision:"))
        .unwrap();

    app.record_fill(fill_report(
        "partial-emergency-unwind-fill",
        first_unwind.order_id.to_string(),
        1,
        99,
        0,
        Some(521),
    ))
    .unwrap();
    assert_eq!(
        app.orders(None)
            .iter()
            .filter(|order| order.order_id.contains(":unwind:decision:"))
            .count(),
        1
    );
    let partial = app.algorithm_runs().pop().unwrap();
    assert_eq!(
        partial
            .exposure
            .as_ref()
            .unwrap()
            .net_leader_filled_quantity,
        Quantity::new(1, 0).unwrap()
    );
    assert_eq!(
        partial.exposure.as_ref().unwrap().unwind_committed_quantity,
        Quantity::new(1, 0).unwrap()
    );

    app.apply_remote_execution_event(RemoteOrderUpdate {
        order_id: OrderId::new(format!("remote:{}", first_unwind.order_id)).unwrap(),
        symbol: symbol("BTCUSDT"),
        status: ExecutionOrderStatus::Canceled,
        fill_quantity: None,
        fill_price: None,
        execution_id: None,
        fee_currency: None,
        fee_amount: None,
        occurred_at_unix_nanos: 522.into(),
        reason: "IOC remainder canceled".into(),
    })
    .unwrap();

    let unwind_orders = app
        .orders(None)
        .into_iter()
        .filter(|order| order.order_id.contains(":unwind:decision:"))
        .collect::<Vec<_>>();
    assert_eq!(unwind_orders.len(), 2);
    let second_unwind = unwind_orders
        .iter()
        .find(|order| order.order_id != first_unwind.order_id)
        .unwrap();
    assert_eq!(second_unwind.quantity, Quantity::new(1, 0).unwrap());
    assert_eq!(second_unwind.limit_price, Some(Price::new(99, 0).unwrap()));
    assert_eq!(
        app.intent("intent:partial-unwind")
            .unwrap()
            .compensation_attempts,
        2
    );
    assert_eq!(
        app.algorithm_runs()[0]
            .actions
            .iter()
            .filter(|action| matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::UnwindImmediate,
                    ..
                }
            ))
            .count(),
        2
    );

    app.record_fill(fill_report(
        "remaining-emergency-unwind-fill",
        second_unwind.order_id.to_string(),
        1,
        99,
        0,
        Some(523),
    ))
    .unwrap();
    assert_eq!(app.algorithm_runs()[0].status, AlgorithmRunStatus::Unwound);
    assert_eq!(
        app.algorithm_runs()[0]
            .exposure
            .as_ref()
            .unwrap()
            .net_leader_filled_quantity,
        Quantity::ZERO
    );
    assert_eq!(
        app.intent("intent:partial-unwind").unwrap().status,
        kairos_execution::IntentStatus::Failed
    );
}

#[test]
fn indeterminate_unwind_requires_reconciliation_and_is_not_reexecuted_after_restart() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(HedgeFailsThenUnwindConfirms {
            submissions: 0,
            unwind_indeterminate: true,
        })),
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    configure_test_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());
    let mut intent = maker_taker_pair_intent("intent:indeterminate-unwind", 2, 1, 0);
    intent.max_slippage_bps = Some(100);
    let state = app.submit_intent(intent).unwrap();

    app.record_fill(fill_report(
        "leader-fill-before-indeterminate-unwind",
        state.order_ids[0].to_string(),
        2,
        100,
        0,
        Some(550),
    ))
    .unwrap();

    assert_eq!(
        app.intent("intent:indeterminate-unwind").unwrap().status,
        kairos_execution::IntentStatus::ReconciliationRequired
    );
    let run = app.algorithm_runs().pop().unwrap();
    assert_eq!(run.status, AlgorithmRunStatus::ReconciliationRequired);
    let unwind_action = run
        .actions
        .iter()
        .find(|action| {
            matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::UnwindImmediate,
                    ..
                }
            )
        })
        .unwrap();
    assert_eq!(unwind_action.status, AlgorithmActionStatus::Indeterminate);
    assert_eq!(
        app.orders(None)
            .iter()
            .filter(|order| order.order_id.contains(":unwind:decision:"))
            .count(),
        1
    );
    drop(app);

    let restored = ExecutionApplication::assemble_for_test(
        "execution",
        None,
        Some(Box::new(FileExecutionStore::new(&path))),
    )
    .unwrap();
    assert_eq!(restored.algorithm_runs(), vec![run]);
    assert_eq!(
        restored
            .orders(None)
            .iter()
            .filter(|order| order.order_id.contains(":unwind:decision:"))
            .count(),
        1
    );
    assert!(
        restored
            .intent("intent:indeterminate-unwind")
            .unwrap()
            .pending_orders
            .is_empty()
    );
}

#[test]
fn staged_unwind_is_resumed_after_crash_without_creating_a_second_action() {
    let shared = Arc::new(Mutex::new(None));
    let mut first = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(HedgeFailsThenUnwindConfirms {
            submissions: 0,
            unwind_indeterminate: false,
        })),
        Some(Box::new(CrashAfterStagingUnwindStore {
            snapshot: Arc::clone(&shared),
            fail_once: true,
        })),
    )
    .unwrap();
    configure_test_access(&mut first);
    attach_simulated_risk(&mut first, test_risk());
    let mut intent = maker_taker_pair_intent("intent:staged-unwind-crash", 2, 1, 0);
    intent.max_slippage_bps = Some(100);
    let state = first.submit_intent(intent).unwrap();
    let error = first
        .record_fill(fill_report(
            "leader-fill-before-staged-unwind",
            state.order_ids[0].to_string(),
            2,
            100,
            0,
            Some(600),
        ))
        .unwrap_err();
    assert!(error.to_string().contains("action and request are durable"));
    drop(first);

    let mut restored = ExecutionApplication::assemble_for_test(
        "execution",
        Some(Box::new(HedgeFailsThenUnwindConfirms {
            submissions: 2,
            unwind_indeterminate: false,
        })),
        Some(Box::new(SharedSnapshotStore {
            snapshot: Arc::clone(&shared),
        })),
    )
    .unwrap();
    configure_test_access(&mut restored);
    attach_simulated_risk(&mut restored, test_risk());
    let before = restored.algorithm_runs().pop().unwrap();
    assert_eq!(before.pending_actions().count(), 1);
    assert_eq!(
        before
            .actions
            .iter()
            .filter(|action| matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::UnwindImmediate,
                    ..
                }
            ))
            .count(),
        1
    );
    assert_eq!(
        restored
            .intent("intent:staged-unwind-crash")
            .unwrap()
            .pending_orders
            .len(),
        1
    );

    assert_eq!(restored.advance_due_intent_orders(u64::MAX, 1).unwrap(), 1);
    let after = restored.algorithm_runs().pop().unwrap();
    assert_eq!(after.pending_actions().count(), 0);
    assert_eq!(
        after
            .actions
            .iter()
            .filter(|action| matches!(
                action.kind,
                AlgorithmActionKind::SubmitChild {
                    execution_style: AlgorithmExecutionStyle::UnwindImmediate,
                    ..
                }
            ))
            .count(),
        1
    );
    assert_eq!(
        restored
            .orders(None)
            .iter()
            .filter(|order| order.order_id.contains(":unwind:decision:"))
            .count(),
        1
    );
    assert_eq!(
        restored
            .intent("intent:staged-unwind-crash")
            .unwrap()
            .status,
        kairos_execution::IntentStatus::Compensating
    );
}

#[test]
fn maker_taker_fill_path_completes_and_restores_with_zero_exposure() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    let state = app
        .submit_intent(maker_taker_pair_intent(
            "intent:maker-taker-complete",
            2,
            1,
            0,
        ))
        .unwrap();
    let leader_order_id = state.order_ids[0].clone();
    app.record_fill(fill_report(
        "maker-taker-leader-complete",
        leader_order_id.to_string(),
        2,
        100,
        0,
        Some(400),
    ))
    .unwrap();
    let hedge_order_id = app
        .orders(None)
        .into_iter()
        .find(|order| order.order_id.contains(":hedge:decision:"))
        .unwrap()
        .order_id;
    app.record_fill(fill_report(
        "maker-taker-hedge-complete",
        hedge_order_id.to_string(),
        2,
        100,
        0,
        Some(401),
    ))
    .unwrap();

    assert_eq!(
        app.intent("intent:maker-taker-complete").unwrap().status,
        kairos_execution::IntentStatus::Satisfied
    );
    let run = app.algorithm_runs().pop().unwrap();
    assert_eq!(run.status, AlgorithmRunStatus::Completed);
    assert_eq!(
        run.exposure.as_ref().unwrap().unhedged_filled_quantity,
        Quantity::ZERO
    );
    assert_eq!(
        run.exposure.as_ref().unwrap().unhedged_after_commitment,
        Quantity::ZERO
    );

    let restored = application(&path);
    assert_eq!(restored.algorithm_runs(), vec![run]);
    assert_eq!(
        restored
            .intent("intent:maker-taker-complete")
            .unwrap()
            .status,
        kairos_execution::IntentStatus::Satisfied
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
    assert!(
        state
            .order_ids
            .iter()
            .all(|order_id| app.orders(None).iter().any(|order| {
                order.order_id == *order_id && order.status == ExecutionOrderStatus::Canceled
            }))
    );
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
        broker_id: "simulated".into(),
        execution_channel: "spot".into(),
        trading_mode: None,
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        websocket_url: "wss://ws-api.binance.com:443/ws-api/v3".into(),
        isolated_symbol: None,
        instruments: Vec::new(),
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
        initial_margin_rate_bps: None,
        margin_rule_id: None,
    })
    .unwrap();
    let mut app = ExecutionApplication::assemble_for_test(
        "execution",
        Some(connection),
        Some(Box::new(FileExecutionStore::new(path))),
    )
    .unwrap();
    configure_test_access(&mut app);
    attach_simulated_risk(&mut app, test_risk());
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
fn intent_idempotency_rejects_any_changed_payload() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let intent = strategy_intent("intent:idempotent-payload", 1, None);
    let mut app = application(&path);
    app.submit_intent_with_idempotency(intent.clone(), "command-payload".into())
        .unwrap();

    let mut changed_quantity = intent.clone();
    changed_quantity.target_quantity = Quantity::new(2, 0).unwrap();
    let error = app
        .submit_intent_with_idempotency(changed_quantity, "command-payload".into())
        .unwrap_err();
    assert!(error.to_string().contains("changed intent payload"));

    let mut changed_algorithm = intent;
    changed_algorithm.algorithm =
        kairos_execution::ExecutionAlgorithmPolicy::Twap(kairos_execution::TwapPolicy {
            slice_count: 2,
            slice_interval: DurationNanos::new(10),
        });
    let error = app
        .submit_intent_with_idempotency(changed_algorithm, "command-payload".into())
        .unwrap_err();
    assert!(error.to_string().contains("changed intent payload"));
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
fn live_writer_takeover_blocks_admission_until_remote_reconciliation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("execution.json");
    let mut app = application(&path);
    app.configure_live_trading(true, true);
    let request = submit_order(
        "takeover-order",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Limit,
        1,
        Some(100),
        None,
    );
    let error = app.prepare_submission(request.clone()).unwrap_err();
    assert!(error.to_string().contains("takeover reconciliation"));

    app.complete_writer_reconciliation();
    assert!(app.prepare_submission(request).is_ok());
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
            market_id: Some(MarketId::new("binance:spot").unwrap()),
            side: OrderSide::Buy,
            order_type: OrderType::Market,
            quantity: "2".parse().unwrap(),
            limit_price: None,
            submitted_at_unix_nanos: 10.into(),
        }],
        market_events: vec![MarketObservation::Quote(Quote {
            scope: crate::application::ObservationScope::Market {
                market_id: "binance:spot".into(),
            },
            instrument_id: "BTCUSDT".into(),
            bid_price: Some("99".into()),
            bid_quantity: Some("10".into()),
            ask_price: Some("100".into()),
            ask_quantity: Some("2".into()),
            observed_at_unix_nanos: 11,
            source_id: "replay".into(),
        })],
        simulation: SimulationConfig {
            fee_bps: "10".parse().unwrap(),
            fee_currency: Some(Currency::new("USDT").unwrap()),
            slippage_bps: "0".parse().unwrap(),
            enforce_quote_quantity: true,
        },
        ..Default::default()
    })
    .unwrap();
    assert_eq!(result.fills.len(), 1);
    assert_eq!(result.fills[0].quantity.to_string(), "2");
    assert_eq!(result.fills[0].price.to_string(), "100");
    assert_eq!(result.fills[0].fee.to_string(), "0.2");
    assert_eq!(
        result.fills[0].execution_market_id.as_deref(),
        Some("binance:spot")
    );
    assert_eq!(result.orders[0].status, SimulationOrderStatus::Filled);
}

#[test]
fn backtest_run_consumes_a_downloaded_bar_and_fills_at_close() {
    let result = BacktestApplication::run(BacktestRequest {
        initial_equity: "1000".parse().unwrap(),
        orders: vec![SimulationOrderRequest {
            order_id: OrderId::new("bar-order-1").unwrap(),
            instrument_id: InstrumentId::new("instrument:equity:US:AAPL:common").unwrap(),
            market_id: None,
            side: OrderSide::Buy,
            order_type: OrderType::Market,
            quantity: "1".parse().unwrap(),
            limit_price: None,
            submitted_at_unix_nanos: 1_699_999_999_000_000_000.into(),
        }],
        market_events: vec![MarketObservation::Bar(kairos_execution::Bar {
            scope: crate::application::ObservationScope::Consolidated {
                instrument_id: "instrument:equity:US:AAPL:common".into(),
                network_id: Some("sip".into()),
            },
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
            order_id: kairos_primitives::execution::OrderId::new("order-1").unwrap(),
            intent_id: None,
            plan_id: None,
            leg_id: None,
            status: ExecutionOrderStatus::Accepted,
            remote_order_id: Some(
                kairos_primitives::integration::RemoteOrderId::new("exchange-1").unwrap(),
            ),
            occurred_at_unix_nanos: 42.into(),
            reason: String::new(),
            fill_id: None,
            filled_quantity: None,
            attempt: None,
        })
        .unwrap();
    audit
        .publish(&ExecutionEvent {
            order_id: kairos_primitives::execution::OrderId::new("order-1").unwrap(),
            intent_id: None,
            plan_id: None,
            leg_id: None,
            status: ExecutionOrderStatus::Accepted,
            remote_order_id: Some(
                kairos_primitives::integration::RemoteOrderId::new("exchange-1").unwrap(),
            ),
            occurred_at_unix_nanos: 42.into(),
            reason: String::new(),
            fill_id: None,
            filled_quantity: None,
            attempt: None,
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
    assert_eq!(
        app.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Reduced
    );
    assert_eq!(app.commitments()[0].remaining_quantity.mantissa(), 6);
    assert_eq!(app.commitments()[0].amount.mantissa(), 6);
    assert_eq!(
        app.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Active
    );
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
    assert_eq!(
        app.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Released
    );
    assert_eq!(
        app.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Consumed
    );

    let restored = application(&path);
    assert_eq!(restored.fills(None).len(), 2);
    assert_eq!(
        restored.commitments()[0].status,
        kairos_execution::application::CommitmentStatus::Released
    );
    assert_eq!(
        restored.risk_reservations()[0].status,
        kairos_execution::application::RiskReservationSagaStatus::Consumed
    );
    assert_eq!(
        restored.orders(None)[0].status,
        ExecutionOrderStatus::Filled
    );
}

#[test]
fn reported_execution_market_does_not_overwrite_the_selected_destination() {
    let directory = tempfile::tempdir().unwrap();
    let mut app = application(&directory.path().join("execution.json"));
    let selected_market = MarketId::new("market:broker:smart").unwrap();
    app.configure_execution_route(
        crate::application::ExecutionRouteCandidate {
            route_id: ExecutionRouteId::new("execution-route:test").unwrap(),
            account_id: None,
            segment_key: None,
            instrument_id: None,
            market_id: Some(selected_market.clone()),
            broker_id: BrokerId::new("broker").unwrap(),
            execution_channel: kairos_primitives::execution::ExecutionChannelCode::new("smart")
                .unwrap(),
            order_entry_symbol: kairos_primitives::execution::OrderEntrySymbol::new("BTC").unwrap(),
            supported_order_types: vec![OrderType::Market],
            supported_options: Vec::new(),
            ready: true,
            initial_margin_rate_bps: Some(10_000),
            margin_rule_id: Some("test:fully-funded".into()),
        },
        ParticipantInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Broker, "broker").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("smart").unwrap()),
            "BTC",
        )
        .unwrap(),
    );
    app.submit(submit_order(
        "smart-route-order",
        None,
        "main",
        "BTCUSDT",
        OrderSide::Buy,
        OrderType::Market,
        1,
        None,
        Some(selected_market.as_str()),
    ))
    .unwrap();
    let mut report = fill_report("smart-fill", "smart-route-order", 1, 100, 0, Some(10));
    report.execution_market_id = Some(MarketId::new("market:exchange:actual").unwrap());
    report.reported_broker_id = Some(BrokerId::new("broker").unwrap());
    app.record_fill(report).unwrap();

    let order = &app.orders(None)[0];
    assert_eq!(
        order
            .selected_route
            .as_ref()
            .and_then(|route| route.destination_market_id.as_deref()),
        Some("market:broker:smart")
    );
    assert_eq!(
        app.fills(None)[0].execution_market_id.as_deref(),
        Some("market:exchange:actual")
    );
}
