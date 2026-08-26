use kairos_account_contract::{account_indexed_environment_path, account_indexed_key};
use kairos_primitives::account::AccountId;
use kairos_primitives::runtime::InstanceIdentity;

#[test]
fn account_indexed_environment_is_partitioned_by_account() {
    let identity = InstanceIdentity::new("workspace", "launch", "instance").unwrap();
    let first = account_indexed_environment_path(
        "/runtime",
        &identity,
        &AccountId::new("primary").unwrap(),
    )
    .unwrap();
    let second = account_indexed_environment_path(
        "/runtime",
        &identity,
        &AccountId::new("secondary").unwrap(),
    )
    .unwrap();
    assert_ne!(first, second);
    assert!(first.ends_with("views/v3/Account/account-primary/epoch-1/current.lmdb"));
}

#[test]
fn account_indexed_keys_are_unambiguous() {
    assert_ne!(
        account_indexed_key(&["spot", "asset:USDT"]).unwrap(),
        account_indexed_key(&["spot:asset", "USDT"]).unwrap()
    );
}
