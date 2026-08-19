use kairos_account_contract::{AccountViewKey, AccountViewKind, account_view_path};

#[test]
fn account_view_resources_are_partitioned_by_runtime_account_and_kind() {
    let current = AccountViewKey::new(
        "account:shared",
        "account:primary",
        AccountViewKind::Current,
    )
    .unwrap();
    let observed_orders = AccountViewKey::new(
        "account:shared",
        "account:primary",
        AccountViewKind::ObservedOrders,
    )
    .unwrap();
    assert_ne!(
        account_view_path("/runtime", &current).unwrap(),
        account_view_path("/runtime", &observed_orders).unwrap()
    );
    assert!(
        account_view_path("/runtime", &current)
            .unwrap()
            .ends_with("current/current.snapshot")
    );
    assert!(
        account_view_path("/runtime", &observed_orders)
            .unwrap()
            .ends_with("observed-orders/current.snapshot")
    );
}

#[test]
fn account_view_key_contains_runtime_and_business_identity() {
    let key = AccountViewKey::new(
        "account:launch-1:instance-1",
        "account:primary",
        AccountViewKind::Current,
    )
    .unwrap();
    assert_eq!(
        key.canonical_key(),
        "runtime=account:launch-1:instance-1;account=account:primary;view=current"
    );
}
