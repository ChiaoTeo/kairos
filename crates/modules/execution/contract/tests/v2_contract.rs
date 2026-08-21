use kairos_execution_contract::event::decode_event;
use kairos_execution_contract::{
    ExecutionRouteCandidateResponse, ExecutionViewKey, ExecutionViewKind, execution_view_path,
};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::execution::{
    ExecutionRouteId, OrderEntrySymbol, OrderOptionCode, OrderType,
};
use kairos_primitives::integration::{ParticipantId, ProviderProductCode};
use kairos_primitives::reference::{InstrumentId, MarketId};

#[test]
fn active_view_resources_are_partitioned_by_workspace_and_kind() {
    let orders = ExecutionViewKey::new(
        "workspace:fixture",
        ExecutionViewKind::ActiveOrders,
        Some("launch:one"),
        Some("instance:one"),
    )
    .unwrap();
    let intents = ExecutionViewKey::new(
        "workspace:fixture",
        ExecutionViewKind::ActiveIntents,
        Some("launch:one"),
        Some("instance:one"),
    )
    .unwrap();
    assert_ne!(
        execution_view_path("/runtime", &orders).unwrap(),
        execution_view_path("/runtime", &intents).unwrap()
    );
    assert!(
        execution_view_path("/runtime", &orders)
            .unwrap()
            .ends_with("active-orders/current.snapshot")
    );
    assert!(
        execution_view_path("/runtime", &intents)
            .unwrap()
            .ends_with("active-intents/current.snapshot")
    );
}

#[test]
fn canonical_view_key_contains_runtime_identity() {
    let key = ExecutionViewKey::new(
        "workspace:fixture",
        ExecutionViewKind::ActiveOrders,
        Some("launch:one"),
        Some("instance:one"),
    )
    .unwrap();
    assert_eq!(
        key.canonical_key(),
        "workspace=workspace:fixture;launch=launch:one;instance=instance:one;view=active-orders"
    );
}

#[test]
fn empty_workspace_identity_is_rejected() {
    assert!(
        ExecutionViewKey::new(
            " ",
            ExecutionViewKind::ActiveOrders,
            None::<String>,
            None::<String>,
        )
        .is_err()
    );
}

#[test]
fn unknown_execution_event_identifier_is_rejected() {
    let mut bytes = vec![0_u8; 8];
    bytes[4..8].copy_from_slice(b"NOPE");
    let error = match decode_event(&bytes) {
        Ok(_) => panic!("unknown root must be rejected"),
        Err(error) => error,
    };
    assert!(
        error
            .to_string()
            .contains("unknown Execution v2 event identifier")
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
        participant_id: ParticipantId::new("okx").unwrap(),
        provider_product: ProviderProductCode::new("swap").unwrap(),
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
fn route_contract_accepts_legacy_provider_symbol_json() {
    let raw = serde_json::json!({
        "route_id": "route:okx:swap",
        "account_id": "main",
        "segment_key": "swap",
        "instrument_id": "instrument:btc-perp",
        "market_id": "market:okx:swap:BTC-USDT-SWAP",
        "participant_id": "okx",
        "provider_product": "swap",
        "provider_symbol": "BTC-USDT-SWAP",
        "supported_order_types": ["market"],
        "supported_options": ["reduce_only"],
        "ready": true
    });

    let route = serde_json::from_value::<ExecutionRouteCandidateResponse>(raw).unwrap();

    assert_eq!(route.order_entry_symbol.as_str(), "BTC-USDT-SWAP");
}

#[test]
fn route_contract_rejects_invalid_semantic_identity() {
    let raw = serde_json::json!({
        "route_id": " ",
        "account_id": null,
        "segment_key": null,
        "instrument_id": null,
        "market_id": null,
        "participant_id": "okx",
        "provider_product": "swap",
        "order_entry_symbol": "BTC-USDT-SWAP",
        "supported_order_types": ["market"],
        "supported_options": [],
        "ready": true
    });
    assert!(serde_json::from_value::<ExecutionRouteCandidateResponse>(raw).is_err());
}
