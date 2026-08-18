use kairos_execution_contract::event::decode_event;
use kairos_execution_contract::{execution_view_path, ExecutionViewKey, ExecutionViewKind};

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
    assert!(execution_view_path("/runtime", &orders)
        .unwrap()
        .ends_with("active-orders/current.snapshot"));
    assert!(execution_view_path("/runtime", &intents)
        .unwrap()
        .ends_with("active-intents/current.snapshot"));
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
    assert!(ExecutionViewKey::new(
        " ",
        ExecutionViewKind::ActiveOrders,
        None::<String>,
        None::<String>,
    )
    .is_err());
}

#[test]
fn unknown_execution_event_identifier_is_rejected() {
    let mut bytes = vec![0_u8; 8];
    bytes[4..8].copy_from_slice(b"NOPE");
    let error = match decode_event(&bytes) {
        Ok(_) => panic!("unknown root must be rejected"),
        Err(error) => error,
    };
    assert!(error
        .to_string()
        .contains("unknown Execution v2 event identifier"));
}
