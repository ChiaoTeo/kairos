//! White-box behavior tests for dependency failure, recovery, and persistence.
//!
//! These tests intentionally exercise private application wiring. Keeping them in
//! the crate avoids turning test doubles into a public Application API.

use kairos_conflux::{
    BlockingOrderCommand as OrderCommand, BlockingOrderQuery as OrderQuery, CommandOutcome,
    ExternalOrder, ExternalOrderQuery, IndeterminateCommand, IntegrationError, OrderEntryEvent,
    OrderEntryRequest, ParticipantInstrumentRef, ParticipantInstrumentTypeRef, ParticipantKind,
    ParticipantRef,
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
    ExecutionApplication, ExecutionError, ExecutionEvent, ExecutionOrderStatus, HedgePolicy,
    MarketObservation, OrderSide, OrderType, Quote, UnknownRemoteOrderResolution,
};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::decimal::{Money, Price, Quantity};
use kairos_primitives::execution::{
    ClientOrderId, ExecutionRouteId, FillId, IntentId, LegId, OrderId,
};
use kairos_primitives::reference::{Currency, InstrumentId, MarketId, Symbol};
use kairos_primitives::time::UnixNanos;

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
        reported_provider_id: None,
        provider_product: None,
        provider_symbol: None,
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
        completion_policy: Default::default(),
        failure_policy: Default::default(),
        legs: Vec::new(),
        deadline_unix_nanos: None,
        min_edge_bps: None,
        max_slippage_bps: None,
        estimated_fee_bps: None,
        minimum_net_credit: None,
        maximum_loss: None,
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

fn application(path: &std::path::Path) -> ExecutionApplication {
    let connection = compose_order_entry(&ExecutionConnectionOptions {
        route_id: "test".into(),
        required: true,
        account_id: "main".into(),
        segment_key: "spot".into(),
        participant_id: "simulated".into(),
        product: "spot".into(),
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
            participant_id: "simulated".into(),
            provider_product: kairos_primitives::integration::ProviderProductCode::new("spot")
                .unwrap(),
            provider_symbol: kairos_primitives::integration::ProviderSymbol::new("BTCUSDT")
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
            participant_id: "simulated".into(),
            provider_product: kairos_primitives::integration::ProviderProductCode::new("spot")
                .unwrap(),
            provider_symbol: kairos_primitives::integration::ProviderSymbol::new("BTCUSDT")
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
            participant_id: "simulated".into(),
            provider_product: kairos_primitives::integration::ProviderProductCode::new("spot")
                .unwrap(),
            provider_symbol: kairos_primitives::integration::ProviderSymbol::new("BTCUSDT")
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
            participant_id: "simulated".into(),
            provider_product: kairos_primitives::integration::ProviderProductCode::new("spot")
                .unwrap(),
            provider_symbol: kairos_primitives::integration::ProviderSymbol::new("BTCUSDT")
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
    assert_eq!(selected.participant_id, "simulated");
    assert_eq!(selected.provider_product, "spot");
    assert_eq!(selected.provider_symbol, "BTCUSDT");
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
        participant_id: "simulated".into(),
        product: "spot".into(),
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
        participant_id: "simulated".into(),
        product: "spot".into(),
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
        participant_id: "simulated".into(),
        product: "spot".into(),
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
        participant_id: "simulated".into(),
        product: "spot".into(),
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
                ratio: kairos_primitives::decimal::Ratio::new(2, 1).unwrap(),
                contract_multiplier: kairos_primitives::decimal::Ratio::new(1, 1).unwrap(),
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
    assert!(
        app.orders(None)
            .iter()
            .any(|order| order.order_id.contains(":compensate:8"))
    );
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
        participant_id: "simulated".into(),
        product: "spot".into(),
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
            participant_id: "broker".into(),
            provider_product: kairos_primitives::integration::ProviderProductCode::new("smart")
                .unwrap(),
            provider_symbol: kairos_primitives::integration::ProviderSymbol::new("BTC").unwrap(),
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
    report.reported_provider_id = Some("broker".into());
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
