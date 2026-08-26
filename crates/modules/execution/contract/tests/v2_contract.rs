use kairos_execution_contract::event::decode_event;
use kairos_execution_contract::{
    CompletionPolicy, ExecutionAlgorithmPolicyRequest, ExecutionHealthResponse,
    ExecutionIntentRequest, ExecutionOrderAuditEventResponse, ExecutionOrderAuditQuery,
    ExecutionOrderAuditResponse, ExecutionOrderLifecycle, ExecutionOrderOptionsRequest,
    ExecutionRouteCandidateResponse, ExecutionViewKey, ExecutionViewKind, FailurePolicy,
    IntentType, SplitOrderPolicyRequest, execution_view_path,
};
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::execution::{
    ExecutionChannelCode, ExecutionRouteId, IntentId, OrderEntrySymbol, OrderOptionCode, OrderType,
};
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::runtime::{InstanceId, LaunchId, StrategyId};

#[test]
fn current_view_resources_are_partitioned_by_runtime_identity() {
    let first = ExecutionViewKey::new(
        "workspace:fixture",
        ExecutionViewKind::CurrentExecution,
        Some("launch:one"),
        Some("instance:one"),
    )
    .unwrap();
    let second = ExecutionViewKey::new(
        "workspace:fixture",
        ExecutionViewKind::CurrentExecution,
        Some("launch:one"),
        Some("instance:two"),
    )
    .unwrap();
    assert_ne!(
        execution_view_path("/runtime", &first).unwrap(),
        execution_view_path("/runtime", &second).unwrap()
    );
    assert!(execution_view_path("/runtime", &first).unwrap().ends_with(
        "launch=launch%3Aone/instance=instance%3Aone/current-execution/current.snapshot"
    ));
}

#[test]
fn canonical_view_key_contains_runtime_identity() {
    let key = ExecutionViewKey::new(
        "workspace:fixture",
        ExecutionViewKind::CurrentExecution,
        Some("launch:one"),
        Some("instance:one"),
    )
    .unwrap();
    assert_eq!(
        key.canonical_key(),
        "workspace=workspace:fixture;launch=launch:one;instance=instance:one;view=current-execution"
    );
}

#[test]
fn empty_workspace_identity_is_rejected() {
    assert!(
        ExecutionViewKey::new(
            " ",
            ExecutionViewKind::CurrentExecution,
            None::<String>,
            None::<String>,
        )
        .is_err()
    );
}

#[test]
fn unknown_execution_event_identifier_is_rejected() {
    for identifier in [b"NOPE", b"EXV2"] {
        let mut bytes = vec![0_u8; 8];
        bytes[4..8].copy_from_slice(identifier);
        let error = match decode_event(&bytes) {
            Ok(_) => panic!("unknown or removed root must be rejected"),
            Err(error) => error,
        };
        assert!(
            error
                .to_string()
                .contains("unknown Execution v2 event identifier")
        );
    }
}

#[test]
fn health_contract_exposes_business_recovery_blockers() {
    let health = ExecutionHealthResponse {
        status: "degraded".into(),
        writer_recovery_ready: true,
        risk_recovery_ready: false,
        risk_recovery_error: Some("risk watermark is stale".into()),
        reconciliation_required_orders: 2,
        reconciliation_required_intents: 1,
        unresolved_remote_orders: 3,
        indeterminate_algorithm_actions: 1,
        outbox_backlog: 4,
        oldest_outbox_event_age_ms: Some(25),
        outbox_error: None,
        routes: Vec::new(),
    };

    let encoded = serde_json::to_value(&health).unwrap();
    assert_eq!(encoded["reconciliation_required_orders"], 2);
    assert_eq!(encoded["unresolved_remote_orders"], 3);
    assert_eq!(encoded["risk_recovery_ready"], false);
    assert_eq!(
        serde_json::from_value::<ExecutionHealthResponse>(encoded).unwrap(),
        health
    );
}

#[test]
fn order_audit_contract_is_typed_and_bounded_by_the_request_surface() {
    let default_query: ExecutionOrderAuditQuery = serde_json::from_value(serde_json::json!({}))
        .expect("optional audit filters have a bounded default");
    assert_eq!(default_query.limit, 1_000);

    let response = ExecutionOrderAuditResponse {
        events: vec![ExecutionOrderAuditEventResponse {
            sequence: 7.into(),
            order_id: kairos_primitives::execution::OrderId::new("order-7").unwrap(),
            lifecycle: ExecutionOrderLifecycle::PartiallyFilled,
            remote_order_id: Some(
                kairos_primitives::integration::RemoteOrderId::new("remote-7").unwrap(),
            ),
            occurred_at_unix_nanos: 42.into(),
            reason: "cumulative fill observed".into(),
            attempt: None,
        }],
    };
    let encoded = serde_json::to_value(&response).unwrap();
    assert_eq!(encoded["events"][0]["lifecycle"], "partially_filled");
    assert_eq!(encoded["events"][0]["order_id"], "order-7");
    assert_eq!(
        serde_json::from_value::<ExecutionOrderAuditResponse>(encoded).unwrap(),
        response
    );
}

#[test]
fn route_contract_uses_order_entry_symbol_in_json_shape() {
    let route = ExecutionRouteCandidateResponse {
        route_id: ExecutionRouteId::new("route:okx:swap").unwrap(),
        account_id: Some(AccountId::new("main").unwrap()),
        segment_key: Some(SegmentKey::new("swap").unwrap()),
        instrument_id: Some(InstrumentId::new("instrument:btc-perp").unwrap()),
        market_id: Some(MarketId::new("market:okx:swap:BTC-USDT-SWAP").unwrap()),
        broker_id: BrokerId::new("okx").unwrap(),
        execution_channel: ExecutionChannelCode::new("swap").unwrap(),
        order_entry_symbol: OrderEntrySymbol::new("BTC-USDT-SWAP").unwrap(),
        supported_order_types: vec![OrderType::Market, OrderType::Limit],
        supported_options: vec![OrderOptionCode::new("reduce_only").unwrap()],
        ready: true,
    };

    let json = serde_json::to_value(&route).unwrap();
    assert_eq!(json["route_id"], "route:okx:swap");
    assert_eq!(json["order_entry_symbol"], "BTC-USDT-SWAP");
    assert_eq!(json["supported_order_types"][0], "market");
    assert_eq!(json["supported_options"][0], "reduce_only");
    assert_eq!(
        serde_json::from_value::<ExecutionRouteCandidateResponse>(json).unwrap(),
        route
    );
}

#[test]
fn route_contract_rejects_removed_provider_symbol_alias() {
    let raw = serde_json::json!({
        "route_id": "route:okx:swap",
        "account_id": "main",
        "segment_key": "swap",
        "instrument_id": "instrument:btc-perp",
        "market_id": "market:okx:swap:BTC-USDT-SWAP",
        "broker_id": "okx",
        "execution_channel": "swap",
        "provider_symbol": "BTC-USDT-SWAP",
        "supported_order_types": ["market"],
        "supported_options": ["reduce_only"],
        "ready": true
    });

    assert!(serde_json::from_value::<ExecutionRouteCandidateResponse>(raw).is_err());
}

#[test]
fn route_contract_rejects_invalid_semantic_identity() {
    let raw = serde_json::json!({
        "route_id": " ",
        "account_id": null,
        "segment_key": null,
        "instrument_id": null,
        "market_id": null,
        "broker_id": "okx",
        "execution_channel": "swap",
        "order_entry_symbol": "BTC-USDT-SWAP",
        "supported_order_types": ["market"],
        "supported_options": [],
        "ready": true
    });
    assert!(serde_json::from_value::<ExecutionRouteCandidateResponse>(raw).is_err());
}

#[test]
fn intent_algorithm_has_one_explicit_tagged_api() {
    let immediate = serde_json::json!({"type": "immediate"});
    assert_eq!(
        serde_json::from_value::<ExecutionAlgorithmPolicyRequest>(immediate).unwrap(),
        ExecutionAlgorithmPolicyRequest::Immediate
    );

    let legacy_implicit_timing = serde_json::json!({"slice_count": 3, "interval": 10});
    assert!(
        serde_json::from_value::<ExecutionAlgorithmPolicyRequest>(legacy_implicit_timing).is_err()
    );

    let legacy_hedge_field = serde_json::json!({"hedge_policy": {}});
    assert!(serde_json::from_value::<ExecutionAlgorithmPolicyRequest>(legacy_hedge_field).is_err());

    let tagged_immediate_with_legacy_hedge =
        serde_json::json!({"type": "immediate", "hedge_policy": {}});
    assert!(
        serde_json::from_value::<ExecutionAlgorithmPolicyRequest>(
            tagged_immediate_with_legacy_hedge
        )
        .is_err()
    );

    let split_with_legacy_interval = serde_json::json!({"child_count": 3, "interval": 10});
    assert!(serde_json::from_value::<SplitOrderPolicyRequest>(split_with_legacy_interval).is_err());

    let mut intent = serde_json::to_value(ExecutionIntentRequest {
        intent_id: IntentId::new("intent:explicit-algorithm").unwrap(),
        strategy_decision_id: None,
        strategy_id: StrategyId::new("strategy:test").unwrap(),
        launch_id: LaunchId::new("launch:test").unwrap(),
        instance_id: InstanceId::new("instance:test").unwrap(),
        instrument_id: InstrumentId::new("instrument:test").unwrap(),
        market_id: None,
        execution_route_id: None,
        account_ids: vec![AccountId::new("account:test").unwrap()],
        segment_key: SegmentKey::new("spot").unwrap(),
        target_quantity: Quantity::new(1, 0).unwrap(),
        limit_price: None,
        source_snapshot_id: None,
        source_event_sequence: None,
        source_event_time_unix_nanos: None,
        reason: "test".into(),
        intent_type: IntentType::SingleOrder,
        algorithm: ExecutionAlgorithmPolicyRequest::Immediate,
        completion_policy: CompletionPolicy::AllLegsSatisfied,
        failure_policy: FailurePolicy::CancelRemaining,
        legs: Vec::new(),
        execution_benchmarks: Vec::new(),
        deadline_unix_nanos: None,
        min_edge_bps: None,
        max_slippage_bps: None,
        estimated_fee_bps: None,
        minimum_net_credit: None,
        maximum_loss: None,
        order_options: ExecutionOrderOptionsRequest::default(),
    })
    .unwrap();
    intent["hedge_policy"] = serde_json::json!({});
    assert!(serde_json::from_value::<ExecutionIntentRequest>(intent).is_err());
}
