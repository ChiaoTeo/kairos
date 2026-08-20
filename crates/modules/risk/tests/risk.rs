use kairos_protocol::generated::kairos::risk::v_2::{
    reservation_reserved_buffer_has_identifier, risk_latest_view_buffer_has_identifier,
    root_as_reservation_reserved, root_as_risk_decision_made, root_as_risk_latest_view,
};
use kairos_risk::composition::{
    FlatbuffersRiskEventWriter, FlatbuffersRiskSnapshotWriter, compose_risk_application,
};
use kairos_risk::{
    Amount, AuthorizeRequest, CircuitScope, CloseCircuit, ConsumeReservation, EnforcementMode,
    Metric, OpenCircuit, PolicyScope, PublishPolicy, ReleaseReservation, ReservationStatus,
    ResizeReservation, RiskApplication, RiskClockMode, RiskContext, RiskPolicy, TradeRiskProposal,
};

fn policy_id(value: &str) -> kairos_primitives::risk::PolicyId {
    kairos_primitives::risk::PolicyId::new(value).unwrap()
}

fn request_id(value: &str) -> kairos_primitives::runtime::RequestId {
    kairos_primitives::runtime::RequestId::new(value).unwrap()
}

fn idempotency_key(value: &str) -> kairos_primitives::runtime::IdempotencyKey {
    kairos_primitives::runtime::IdempotencyKey::new(value).unwrap()
}

fn reservation_id(value: &str) -> kairos_primitives::risk::ReservationId {
    kairos_primitives::risk::ReservationId::new(value).unwrap()
}

fn strategy_id(value: &str) -> kairos_primitives::runtime::StrategyId {
    kairos_primitives::runtime::StrategyId::new(value).unwrap()
}

fn amount(value: i64) -> Amount {
    Amount::new(value, 0).unwrap()
}

fn policy(id: &str, limit: i64, account: &str) -> RiskPolicy {
    RiskPolicy {
        policy_id: policy_id(id),
        version: 1.into(),
        scope: PolicyScope {
            account_id: Some(kairos_primitives::account::AccountId::new(account).unwrap()),
            strategy_id: None,
            instrument_id: None,
            exchange_id: None,
        },
        metric: Metric::Notional,
        limit: amount(limit),
        enforcement: EnforcementMode::Reject,
        valid_from_unix_nanos: 0.into(),
        valid_until_unix_nanos: None,
        window_nanos: None,
    }
}

fn application(limit: i64) -> RiskApplication {
    compose_risk_application(
        "risk",
        vec![policy("account-notional", limit, "main")],
        None,
    )
    .unwrap()
}

fn request(id: &str, value: i64) -> AuthorizeRequest {
    AuthorizeRequest {
        request_id: request_id(id),
        idempotency_key: idempotency_key(&format!("key:{id}")),
        reservation_id: reservation_id(&format!("reservation:{id}")),
        account_id: kairos_primitives::account::AccountId::new("main").unwrap(),
        strategy_id: strategy_id("strategy"),
        instrument_id: kairos_primitives::reference::InstrumentId::new("instrument").unwrap(),
        exchange_id: kairos_primitives::reference::Exchange::new("exchange").unwrap(),
        proposal: TradeRiskProposal {
            notional: amount(value),
            initial_margin_rate_bps: 10_000.into(),
            account_segment: kairos_primitives::account::SegmentKey::new("usd-m").unwrap(),
            collateral_asset: kairos_primitives::reference::Currency::new("USDT").unwrap(),
            reduce_only: false,
            margin_rule_id: "test:fully-funded".into(),
        },
        at_unix_nanos: 1.into(),
        reservation_ttl_nanos: 100.into(),
        dependency_generation: 1.into(),
        dependency_event_sequence: 1.into(),
        context: None,
    }
}

#[test]
fn rejected_authorization_does_not_mutate_limits() {
    let mut app = application(100);
    let result = app.authorize_and_reserve(request("rejected", 101)).unwrap();
    assert!(!result.allowed);
    assert_eq!(app.snapshot().limits[0].available, amount(100));
}

#[test]
fn authorization_is_atomic_and_idempotent() {
    let mut app = application(100);
    let first = app.authorize_and_reserve(request("order", 40)).unwrap();
    let second = app.authorize_and_reserve(request("order", 40)).unwrap();
    assert!(first.allowed);
    assert_eq!(first.reservation, second.reservation);
    assert_eq!(app.snapshot().limits[0].reserved, amount(40));
}

#[test]
fn consume_and_release_are_terminal_and_update_timestamp() {
    let mut app = application(100);
    app.authorize_and_reserve(request("order", 40)).unwrap();
    let consumed = app
        .consume(ConsumeReservation {
            reservation_id: reservation_id("reservation:order"),
            at_unix_nanos: 2.into(),
        })
        .unwrap();
    assert_eq!(consumed.status, ReservationStatus::Consumed);
    assert_eq!(consumed.updated_at_unix_nanos, 2.into());
    // Point-in-time budgets are replaced by the next Account observation;
    // consuming an order must not accumulate them permanently.
    assert_eq!(app.snapshot().limits[0].used, amount(0));
    assert_eq!(app.snapshot().limits[0].reserved, amount(0));
    assert!(
        app.release(ReleaseReservation {
            reservation_id: reservation_id("reservation:order"),
            at_unix_nanos: 3.into(),
        })
        .is_err()
    );
}

#[test]
fn all_matching_hierarchical_policies_must_pass() {
    let mut app = compose_risk_application(
        "risk",
        vec![
            policy("account", 100, "main"),
            policy("account-2", 50, "main"),
        ],
        None,
    )
    .unwrap();
    let result = app.authorize_and_reserve(request("order", 60)).unwrap();
    assert!(!result.allowed);
    assert_eq!(app.snapshot().limits[0].reserved, amount(0));
    assert_eq!(app.snapshot().limits[1].reserved, amount(0));
}

#[test]
fn ttl_expiration_releases_capacity() {
    let mut app = application(100);
    app.authorize_and_reserve(request("order", 40)).unwrap();
    assert_eq!(
        app.expire(kairos_risk::ExpireReservations {
            at_unix_nanos: 101.into()
        })
        .unwrap(),
        1
    );
    assert_eq!(app.snapshot().limits[0].available, amount(100));
    assert_eq!(
        app.snapshot().reservations[0].status,
        ReservationStatus::Expired
    );
}

#[test]
fn replay_clock_ignores_wall_ticks_and_advances_through_one_business_barrier() {
    let mut app = application(100);
    app.set_clock_mode(RiskClockMode::Replay);
    app.authorize_and_reserve(request("replay", 40)).unwrap();

    assert_eq!(app.maintenance_tick(101.into()).unwrap(), 0);
    assert_eq!(app.snapshot().limits[0].reserved, amount(40));

    assert_eq!(app.advance_business_time(101.into()).unwrap(), 1);
    assert_eq!(app.business_time(), Some(101.into()));
    assert_eq!(app.snapshot().limits[0].available, amount(100));
}

#[test]
fn business_time_is_monotonic_inside_the_application_boundary() {
    let mut app = application(100);
    app.set_clock_mode(RiskClockMode::Replay);
    app.advance_business_time(50.into()).unwrap();

    let error = app.advance_business_time(49.into()).unwrap_err();
    assert_eq!(
        error,
        kairos_risk::RiskError::Invalid("business time cannot move backwards".into())
    );
    assert_eq!(app.business_time(), Some(50.into()));
}

#[test]
fn current_view_and_reservation_event_use_independent_writers() {
    let mut app = application(100);
    app.authorize_and_reserve(request("order", 40)).unwrap();
    let snapshot = app.current_view();
    let mut writer = FlatbuffersRiskSnapshotWriter::new("risk");
    writer.publish(&snapshot).unwrap();
    let payload = writer.last_payload.unwrap();
    assert!(risk_latest_view_buffer_has_identifier(&payload));
    assert_eq!(
        root_as_risk_latest_view(&payload)
            .unwrap()
            .state()
            .limits()
            .len(),
        1
    );

    while !matches!(
        app.pending_event(),
        Some(kairos_risk::RiskEvent::ReservationChanged { .. })
    ) {
        app.acknowledge_event();
    }
    let event = app.pending_event().cloned().unwrap();
    let mut event_writer = FlatbuffersRiskEventWriter::new("risk");
    event_writer.publish(&event).unwrap();
    let payload = event_writer.last_payload.unwrap();
    assert!(reservation_reserved_buffer_has_identifier(&payload));
    assert_eq!(
        root_as_reservation_reserved(&payload)
            .unwrap()
            .reservation()
            .reservation_id(),
        "reservation:order"
    );
}

#[test]
fn risk_event_preserves_launch_instance_identity() {
    let mut app = application(100);
    app.authorize_and_reserve(request("identity", 40)).unwrap();
    while !matches!(
        app.pending_event(),
        Some(kairos_risk::RiskEvent::ReservationChanged { .. })
    ) {
        app.acknowledge_event();
    }
    let mut writer = FlatbuffersRiskEventWriter::new_with_identity(
        "risk",
        kairos_primitives::runtime::InstanceIdentity::new("workspace", "launch", "instance")
            .unwrap(),
    );
    writer.publish(app.pending_event().unwrap()).unwrap();
    let payload = writer.last_payload.unwrap();
    let metadata = root_as_reservation_reserved(&payload).unwrap().metadata();
    assert_eq!(metadata.workspace_id(), "workspace");
    assert_eq!(metadata.launch_id(), Some("launch"));
    assert_eq!(metadata.instance_id(), Some("instance"));
}

#[test]
fn journal_recovers_reservation_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("risk-state.json");
    {
        let mut app =
            compose_risk_application("risk", vec![policy("p", 100, "main")], Some(path.clone()))
                .unwrap();
        app.authorize_and_reserve(request("order", 40)).unwrap();
    }
    let restored = compose_risk_application("risk", Vec::new(), Some(path)).unwrap();
    assert_eq!(restored.snapshot().limits[0].reserved, amount(40));
    assert_eq!(
        restored.snapshot().reservations[0].status,
        ReservationStatus::Reserved
    );
}

#[test]
fn publish_policy_requires_monotonic_versions() {
    let mut app = application(100);
    assert!(
        app.publish_policy(PublishPolicy {
            policy: policy("account-notional", 200, "main")
        })
        .is_err()
    );
    let mut next = policy("account-notional", 200, "main");
    next.version = 2.into();
    app.publish_policy(PublishPolicy { policy: next }).unwrap();
    assert_eq!(app.snapshot().limits[0].policy.limit, amount(200));
}

#[test]
fn pre_trade_rejects_stale_market_and_insufficient_margin() {
    let mut app = compose_risk_application(
        "risk",
        vec![RiskPolicy {
            policy_id: policy_id("margin"),
            version: 1.into(),
            scope: PolicyScope {
                account_id: Some(kairos_primitives::account::AccountId::new("main").unwrap()),
                strategy_id: None,
                instrument_id: None,
                exchange_id: None,
            },
            metric: Metric::Margin,
            limit: amount(100),
            enforcement: EnforcementMode::Reject,
            valid_from_unix_nanos: 0.into(),
            valid_until_unix_nanos: None,
            window_nanos: None,
        }],
        None,
    )
    .unwrap();
    let mut request = request("margin-check", 120);
    request.context = Some(RiskContext {
        available_margin: amount(100),
        market_is_fresh: false,
        ..RiskContext::default()
    });
    let decision = app.pre_trade_check(request).unwrap();
    assert!(!decision.allowed);
    assert!(
        decision
            .reason_codes
            .contains(&kairos_risk::ReasonCode::StaleMarket)
    );
    assert!(
        decision
            .reason_codes
            .contains(&kairos_risk::ReasonCode::InsufficientMargin)
    );
}

#[test]
fn circuit_blocks_and_resume_restores_admission() {
    let mut app = application(100);
    let scope = CircuitScope {
        account_id: Some(kairos_primitives::account::AccountId::new("main").unwrap()),
        strategy_id: None,
        exchange_id: None,
    };
    app.open_circuit(OpenCircuit {
        scope: scope.clone(),
        at_unix_nanos: 2.into(),
        reset_at_unix_nanos: None,
        reason: "daily loss".into(),
    })
    .unwrap();
    let blocked = app.authorize_and_reserve(request("blocked", 10)).unwrap();
    assert!(!blocked.allowed);
    assert!(
        blocked
            .reason_codes
            .contains(&kairos_risk::ReasonCode::CircuitOpen)
    );
    app.close_circuit(CloseCircuit {
        scope,
        at_unix_nanos: 3.into(),
    })
    .unwrap();
    assert!(
        app.authorize_and_reserve(request("resumed", 10))
            .unwrap()
            .allowed
    );
}

#[test]
fn exposure_context_is_checked_against_policy_before_reservation() {
    let mut exposure_policy = policy("gross", 100, "main");
    exposure_policy.metric = Metric::GrossExposure;
    let mut app = compose_risk_application("risk", vec![exposure_policy], None).unwrap();
    let mut order = request("exposure", 30);
    order.context = Some(RiskContext {
        current_exposure: amount(80),
        available_margin: amount(1_000),
        market_is_fresh: true,
        ..RiskContext::default()
    });
    let decision = app.authorize_and_reserve(order).unwrap();
    assert!(!decision.allowed);
    assert!(
        decision
            .reason_codes
            .contains(&kairos_risk::ReasonCode::LimitExceeded)
    );
    assert!(app.snapshot().reservations.is_empty());
}

#[test]
fn proposal_calculates_margin_and_returns_a_structured_shortfall() {
    let mut margin = policy("margin", 50, "main");
    margin.metric = Metric::Margin;
    let mut app = compose_risk_application(
        "risk",
        vec![policy("notional", 1_000, "main"), margin],
        None,
    )
    .unwrap();
    let mut order = request("funding-shortfall", 100);
    order.proposal.initial_margin_rate_bps = 1_000.into();
    order.proposal.margin_rule_id = "reference:binance-usdm:tier-1:v1".into();
    order.context = Some(RiskContext {
        available_margin: amount(9),
        ..RiskContext::default()
    });

    let decision = app.authorize_and_reserve(order).unwrap();
    assert!(!decision.allowed);
    let funding = decision.funding_requirement.unwrap();
    assert_eq!(funding.required_margin, amount(10));
    assert_eq!(funding.available_margin, amount(9));
    assert_eq!(funding.shortfall, amount(1));
    assert_eq!(funding.margin_rule_id, "reference:binance-usdm:tier-1:v1");
    assert_eq!(funding.account_segment.as_str(), "usd-m");
    assert_eq!(funding.collateral_asset.as_str(), "USDT");
    assert!(app.snapshot().reservations.is_empty());

    while !matches!(
        app.pending_event(),
        Some(kairos_risk::RiskEvent::DecisionEvaluated { .. })
    ) {
        app.acknowledge_event();
    }
    let mut writer = FlatbuffersRiskEventWriter::new("risk");
    writer.publish(app.pending_event().unwrap()).unwrap();
    let payload = writer.last_payload.unwrap();
    let encoded = root_as_risk_decision_made(&payload)
        .unwrap()
        .decision()
        .funding_requirement()
        .unwrap();
    assert_eq!(encoded.required_margin().mantissa(), 10);
    assert_eq!(encoded.shortfall().mantissa(), 1);
    assert_eq!(encoded.margin_rule_id(), "reference:binance-usdm:tier-1:v1");
    assert_eq!(encoded.account_segment(), "usd-m");
    assert_eq!(encoded.collateral_asset(), "USDT");
}

#[test]
fn proposal_reserves_all_configured_metrics_atomically() {
    let mut margin = policy("margin", 10, "main");
    margin.metric = Metric::Margin;
    let mut app =
        compose_risk_application("risk", vec![policy("notional", 100, "main"), margin], None)
            .unwrap();
    let mut order = request("multi-metric", 100);
    order.proposal.initial_margin_rate_bps = 1_000.into();
    order.context = Some(RiskContext {
        available_margin: amount(10),
        ..RiskContext::default()
    });

    let decision = app.authorize_and_reserve(order).unwrap();
    assert!(decision.allowed);
    let reservation = decision.reservation.unwrap();
    assert!(
        reservation
            .allocations
            .iter()
            .any(|value| value.metric == Metric::Notional && value.amount == amount(100))
    );
    assert!(
        reservation
            .allocations
            .iter()
            .any(|value| value.metric == Metric::Margin && value.amount == amount(10))
    );
    let resized = app
        .resize(ResizeReservation {
            reservation_id: reservation.reservation_id,
            amount: amount(50),
            at_unix_nanos: 2.into(),
        })
        .unwrap();
    assert!(
        resized
            .allocations
            .iter()
            .any(|value| value.metric == Metric::Notional && value.amount == amount(50))
    );
    assert!(
        resized
            .allocations
            .iter()
            .any(|value| value.metric == Metric::Margin && value.amount == amount(5))
    );
}

#[test]
fn circuit_state_is_recovered_from_the_journal() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("risk-state.json");
    let scope = CircuitScope {
        account_id: Some(kairos_primitives::account::AccountId::new("main").unwrap()),
        strategy_id: None,
        exchange_id: None,
    };
    {
        let mut app =
            compose_risk_application("risk", vec![policy("p", 100, "main")], Some(path.clone()))
                .unwrap();
        app.open_circuit(OpenCircuit {
            scope,
            at_unix_nanos: 10.into(),
            reset_at_unix_nanos: None,
            reason: "manual halt".into(),
        })
        .unwrap();
    }
    let restored = compose_risk_application("risk", Vec::new(), Some(path)).unwrap();
    assert_eq!(restored.circuits().len(), 1);
    assert!(restored.circuits()[0].open);
}

#[test]
fn circuit_state_is_published_in_the_risk_current_view() {
    let mut app = application(100);
    app.open_circuit(OpenCircuit {
        scope: CircuitScope {
            account_id: Some(kairos_primitives::account::AccountId::new("main").unwrap()),
            strategy_id: None,
            exchange_id: None,
        },
        at_unix_nanos: 10.into(),
        reset_at_unix_nanos: None,
        reason: "risk halt".into(),
    })
    .unwrap();
    let snapshot = app.current_view();
    let mut writer = FlatbuffersRiskSnapshotWriter::new("risk");
    writer.publish(&snapshot).unwrap();
    let payload = writer.last_payload.unwrap();
    let root = root_as_risk_latest_view(&payload).unwrap();
    assert_eq!(root.state().circuits().len(), 1);
}

#[test]
fn reservation_resize_is_atomic_and_keeps_the_same_identity() {
    let mut app = application(100);
    let first = app.authorize_and_reserve(request("resize", 60)).unwrap();
    let reservation_id = first.reservation.as_ref().unwrap().reservation_id.clone();
    let updated = app
        .resize(ResizeReservation {
            reservation_id,
            amount: amount(25),
            at_unix_nanos: 2.into(),
        })
        .unwrap();
    assert_eq!(updated.reservation_id, "reservation:resize");
    assert_eq!(updated.allocations[0].amount, amount(25));
    assert_eq!(app.snapshot().limits[0].reserved, amount(25));
}

#[test]
fn leverage_and_drawdown_policies_are_checked_from_context() {
    let mut leverage = policy("leverage", 2_000, "main");
    leverage.metric = Metric::Leverage;
    let mut drawdown = policy("drawdown", 500, "main");
    drawdown.metric = Metric::Drawdown;
    let mut app = compose_risk_application("risk", vec![leverage, drawdown], None).unwrap();
    let mut leverage_request = request("leverage", 1);
    leverage_request.context = Some(RiskContext {
        leverage_bps: 2_500.into(),
        ..RiskContext::default()
    });
    let decision = app.pre_trade_check(leverage_request).unwrap();
    assert!(!decision.allowed);
    assert!(
        decision
            .reason_codes
            .contains(&kairos_risk::ReasonCode::LeverageExceeded)
    );

    let mut request = request("drawdown", 1);
    request.context = Some(RiskContext {
        current_drawdown: amount(600),
        ..RiskContext::default()
    });
    let decision = app.pre_trade_check(request).unwrap();
    assert!(!decision.allowed);
    assert!(
        decision
            .reason_codes
            .contains(&kairos_risk::ReasonCode::LossLimitExceeded)
    );
}

#[test]
fn circuit_reset_time_allows_a_new_admission_window() {
    let mut app = application(100);
    app.open_circuit(OpenCircuit {
        scope: CircuitScope {
            account_id: Some(kairos_primitives::account::AccountId::new("main").unwrap()),
            strategy_id: None,
            exchange_id: None,
        },
        at_unix_nanos: 10.into(),
        reset_at_unix_nanos: Some(20.into()),
        reason: "cooldown".into(),
    })
    .unwrap();
    let blocked = app
        .authorize_and_reserve(request("before-reset", 10))
        .unwrap();
    assert!(!blocked.allowed);
    let mut after_reset = request("after-reset", 10);
    after_reset.at_unix_nanos = 20.into();
    let allowed = app.authorize_and_reserve(after_reset).unwrap();
    assert!(allowed.allowed);
}

#[test]
fn order_rate_policy_limits_requests_inside_its_window() {
    let mut order_rate = policy("rate", 2, "main");
    order_rate.metric = Metric::OrderRate;
    order_rate.window_nanos = Some(100.into());
    let mut app = compose_risk_application("risk", vec![order_rate], None).unwrap();
    for id in ["rate-1", "rate-2"] {
        let order = request(id, 1);
        assert!(app.authorize_and_reserve(order).unwrap().allowed);
    }
    let rejected = request("rate-3", 1);
    let decision = app.authorize_and_reserve(rejected).unwrap();
    assert!(!decision.allowed);
    assert!(
        decision
            .reason_codes
            .contains(&kairos_risk::ReasonCode::LimitExceeded)
    );
}

#[test]
fn price_deviation_and_stress_loss_policies_are_context_driven() {
    let mut price = policy("price", 50, "main");
    price.metric = Metric::PriceDeviation;
    let mut stress = policy("stress", 100, "main");
    stress.metric = Metric::StressLoss;
    let mut app = compose_risk_application("risk", vec![price, stress], None).unwrap();

    let mut price_request = request("price", 1);
    price_request.context = Some(RiskContext {
        price_deviation_bps: 75.into(),
        ..RiskContext::default()
    });
    assert!(!app.pre_trade_check(price_request).unwrap().allowed);

    let mut stress_request = request("stress", 1);
    stress_request.context = Some(RiskContext {
        stress_loss: amount(150),
        ..RiskContext::default()
    });
    assert!(!app.pre_trade_check(stress_request).unwrap().allowed);
}
