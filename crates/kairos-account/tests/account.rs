use std::collections::BTreeMap;

use kairos_account::application::{AccountStateStore, OrderRisk, OrderRiskRequest};
use kairos_account::composition::account::{
    compose_account_application, compose_account_application_for_segments, AccountOptions,
    AccountRegistry, CredentialRecord, CredentialStore,
};
use kairos_account::composition::{
    empty_snapshot, FlatbuffersAccountPublisher, InMemoryAccountSource, JsonAccountStore,
};
use kairos_account::domain::{
    Account, AccountEvent, AccountFill, AccountSegment, AccountSnapshot, AccountStatus, Balance,
    DecimalValue, ExternalAccountIdentity, FillSide, OrderRequest, OrderSide, OrderType, Position,
};
use kairos_account::{
    AccountApplication, AccountDataQuery, AccountQuery, ReconcileAccount, RefreshAccount,
};
use kairos_protocol::generated::kairos::account::v_1::root_as_accounts_snapshot;
use kairos_protocol::InstanceIdentity;

fn segment(key: &str) -> AccountSegment {
    AccountSegment {
        identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
        segment_key: key.into(),
        environment: "paper".into(),
        account_model: Some("no_margin".into()),
    }
}

#[test]
fn credential_can_resolve_secret_from_namespaced_environment() {
    let name = "KAIROS_CREDENTIAL_TEST_ACCOUNT_API_SECRET";
    std::env::set_var(name, "secret-from-env");
    let credential = CredentialRecord {
        credential_id: "test-account".into(),
        provider: "binance".into(),
        role: "readonly".into(),
        api_key: String::new(),
        secret: String::new(),
        passphrase: String::new(),
    };
    assert_eq!(
        credential.secret_value().as_deref(),
        Some("secret-from-env")
    );
    std::env::remove_var(name);
}

#[test]
fn credential_store_persists_toml_records() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("credentials/credentials.toml");
    let credentials = vec![CredentialRecord {
        credential_id: "binance-live".into(),
        provider: "binance".into(),
        role: "readonly".into(),
        api_key: "stored-key".into(),
        secret: "stored-secret".into(),
        passphrase: String::new(),
    }];
    let store = CredentialStore { credentials };
    store.save(&path).unwrap();
    let loaded = CredentialStore::load(&path).unwrap();
    assert_eq!(loaded.credentials, store.credentials);
    assert!(directory
        .path()
        .join("credentials/binance-live.toml")
        .is_file());
}

#[test]
fn registry_and_credentials_load_per_record_toml_files() {
    let directory = tempfile::tempdir().unwrap();
    let accounts = directory.path().join("accounts");
    let credentials = directory.path().join("credentials");
    std::fs::create_dir_all(&accounts).unwrap();
    std::fs::create_dir_all(&credentials).unwrap();
    std::fs::write(
        accounts.join("main.toml"),
        r#"[account]
id = "main"
broker = "binance"
environment = "live"

[segments.spot]
product_family = "spot"

[credentials.readonly]
ref = "binance-read"
role = "trade"
"#,
    )
    .unwrap();
    std::fs::write(
        credentials.join("binance-read.toml"),
        r#"[credential]
id = "binance-read"
broker = "binance"
api_key = "key"
api_secret = "secret"
"#,
    )
    .unwrap();
    let registry = AccountRegistry::load(accounts.join("accounts.toml")).unwrap();
    assert_eq!(registry.accounts[0].account_id, "main");
    assert_eq!(registry.accounts[0].segments, vec!["spot"]);
    assert_eq!(
        registry.accounts[0].credential_role.as_deref(),
        Some("trade")
    );
    assert_eq!(registry.accounts[0].credentials[0].role, "trade");
    let store = CredentialStore::load(credentials.join("credentials.toml")).unwrap();
    assert_eq!(store.credentials[0].credential_id, "binance-read");
    assert_eq!(store.credentials[0].api_key, "key");
}

#[test]
fn paper_account_composition_is_local_and_does_not_require_credentials() {
    let directory = tempfile::tempdir().unwrap();
    let options = AccountOptions {
        provider: "paper".into(),
        product: "spot".into(),
        api_key: String::new(),
        secret: String::new(),
        passphrase: String::new(),
        base_url: "https://api.binance.com".into(),
        account_id: "paper-main".into(),
        segment: "spot".into(),
        environment: "paper".into(),
        account_model: None,
        initial_balances: vec!["USDT=10000.50".into()],
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    };
    let mut composition =
        compose_account_application(&options, Some(directory.path().join("account.json"))).unwrap();
    assert_eq!(composition.provider, "paper");
    assert_eq!(
        composition
            .application
            .refresh(RefreshAccount {
                account_id: "paper-main".into(),
                segments: vec![],
            })
            .unwrap(),
        1
    );
    assert_eq!(composition.application.snapshot().accounts.len(), 1);
    assert_eq!(
        composition.application.balances(Some("paper-main"))[0].2[0].total,
        DecimalValue::new(1_000_050, 2)
    );
}

#[test]
fn paper_account_composition_restores_multiple_configured_segments() {
    let directory = tempfile::tempdir().unwrap();
    let options = AccountOptions {
        provider: "paper".into(),
        product: "spot".into(),
        api_key: String::new(),
        secret: String::new(),
        passphrase: String::new(),
        base_url: String::new(),
        account_id: "paper-main".into(),
        segment: "spot".into(),
        environment: "paper".into(),
        account_model: None,
        initial_balances: Vec::new(),
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    };
    let mut composition = compose_account_application_for_segments(
        &options,
        &["spot".into(), "margin".into()],
        Some(directory.path().join("account.json")),
    )
    .unwrap();
    composition
        .application
        .refresh(RefreshAccount {
            account_id: "paper-main".into(),
            segments: vec![],
        })
        .unwrap();
    assert_eq!(composition.application.snapshot().accounts.len(), 2);
}

#[test]
fn account_application_exposes_capabilities_and_fee_queries() {
    let directory = tempfile::tempdir().unwrap();
    let options = AccountOptions {
        provider: "paper".into(),
        product: "spot".into(),
        api_key: String::new(),
        secret: String::new(),
        passphrase: String::new(),
        base_url: String::new(),
        account_id: "paper-main".into(),
        segment: "spot".into(),
        environment: "paper".into(),
        account_model: None,
        initial_balances: Vec::new(),
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    };
    let composition = compose_account_application_for_segments(
        &options,
        &["spot".into(), "margin".into()],
        Some(directory.path().join("account.json")),
    )
    .unwrap();
    let capabilities = composition.application.capabilities(Some("paper-main"));
    assert_eq!(capabilities.len(), 2);
    assert!(capabilities.iter().all(|value| value.can_hold_assets));
    assert!(capabilities
        .iter()
        .all(|value| !value.can_transfer_in && !value.can_transfer_out));
    assert!(
        !capabilities
            .iter()
            .find(|value| value.segment_key == "spot")
            .unwrap()
            .can_hold_position
    );
    assert_eq!(
        composition
            .application
            .fee_schedules(Some("paper-main"))
            .len(),
        2
    );
}

#[test]
fn ibkr_account_composition_selects_native_equity_connection() {
    let options = AccountOptions {
        provider: "ibkr".into(),
        product: "equity".into(),
        api_key: String::new(),
        secret: String::new(),
        passphrase: String::new(),
        base_url: String::new(),
        account_id: "DU123".into(),
        segment: "equity".into(),
        environment: "live".into(),
        account_model: None,
        initial_balances: Vec::new(),
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    };
    let composition = compose_account_application(&options, None).unwrap();
    assert_eq!(composition.provider, "ibkr");
}

#[test]
fn refresh_owns_segment_state_and_query_returns_typed_view() {
    let source = InMemoryAccountSource {
        snapshots: BTreeMap::from([(
            "spot".into(),
            AccountSnapshot {
                segment_key: "spot".into(),
                balances: vec![Balance {
                    asset_id: "asset:usdt".into(),
                    asset_code: "USDT".into(),
                    total: DecimalValue::new(10_000, 2),
                    ..Default::default()
                }],
                collateral: vec![],
                positions: vec![Position {
                    instrument_id: "instrument:btc".into(),
                    quantity: DecimalValue::new(25, 2),
                    ..Default::default()
                }],
                open_orders: vec![],
                status: AccountStatus::Ready,
                observed_at_unix_nanos: 42,
                equity: Some(DecimalValue::new(10_000, 2)),
                initial_equity: None,
                net_profit: None,
                account_model: None,
                margin_mode: None,
                position_mode: None,
                partial: false,
            },
        )]),
    };
    let mut app =
        AccountApplication::with_dependencies(vec![segment("spot")], Box::new(source), None)
            .unwrap();

    assert_eq!(
        app.refresh(RefreshAccount {
            account_id: "main".into(),
            segments: vec![]
        })
        .unwrap(),
        1
    );
    let result = app
        .query(AccountQuery {
            account_id: "main".into(),
            segments: vec![],
            max_age_seconds: None,
            now_unix_nanos: None,
        })
        .unwrap();
    assert_eq!(result.len(), 1);
    assert_eq!(
        result[0].account.state.balances["asset:usdt"].total,
        DecimalValue::new(10_000, 2)
    );
    assert_eq!(
        result[0].account.state.positions["instrument:btc"].quantity,
        DecimalValue::new(25, 2)
    );
    app.apply_fill(AccountFill {
        fill_id: None,
        order_id: None,
        segment_key: "spot".into(),
        instrument_id: "instrument:btc".into(),
        quantity: DecimalValue::new(1, 2),
        price: DecimalValue::new(100, 0),
        side: FillSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: 43,
    })
    .unwrap();
    let reconciliation = app
        .reconcile_report(ReconcileAccount {
            account_id: "main".into(),
            segments: vec![],
        })
        .unwrap();
    assert!(reconciliation
        .differences
        .iter()
        .any(|value| value.field == "position.quantity" && value.key == "instrument:btc"));
    let filtered = app.balances_query(&AccountDataQuery {
        account_id: Some("main".into()),
        segments: vec!["spot".into()],
        page: Some(1),
        page_size: Some(10),
        ..Default::default()
    });
    assert_eq!(filtered.len(), 1);
    assert_eq!(filtered[0].2[0].asset_code, "USDT");
    let positions = app.positions_query(&AccountDataQuery {
        account_id: Some("main".into()),
        symbol: Some("btc".into()),
        ..Default::default()
    });
    assert_eq!(positions[0].2.len(), 1);
}

#[test]
fn publisher_emits_current_account_snapshot() {
    let source = InMemoryAccountSource {
        snapshots: BTreeMap::from([("spot".into(), empty_snapshot("spot"))]),
    };
    let mut app =
        AccountApplication::with_dependencies(vec![segment("spot")], Box::new(source), None)
            .unwrap();
    app.refresh(RefreshAccount {
        account_id: "main".into(),
        segments: vec![],
    })
    .unwrap();

    let mut publisher = FlatbuffersAccountPublisher::new_with_identity(
        "account-1",
        InstanceIdentity::new("demo", "btc-sma", "run-001"),
    );
    publisher.publish(&app.snapshot()).unwrap();
    let payload = publisher.last_payload.as_ref().unwrap();
    let decoded = root_as_accounts_snapshot(payload).unwrap();
    assert_eq!(decoded.header().workspace_id(), Some("demo"));
    assert_eq!(decoded.header().launch_id(), Some("btc-sma"));
    assert_eq!(decoded.header().instance_id(), Some("run-001"));
    assert_eq!(decoded.payload().account_count(), 1);
    assert_eq!(
        decoded.payload().accounts().unwrap().get(0).segment_key(),
        "spot"
    );
}

#[test]
fn fill_event_updates_account_position_owned_by_actor() {
    let source = InMemoryAccountSource {
        snapshots: BTreeMap::from([("spot".into(), empty_snapshot("spot"))]),
    };
    let mut app =
        AccountApplication::with_dependencies(vec![segment("spot")], Box::new(source), None)
            .unwrap();
    app.refresh(RefreshAccount {
        account_id: "main".into(),
        segments: vec![],
    })
    .unwrap();
    app.attach_stream(Box::new(TestStream {
        events: vec![Some(AccountEvent::Fill(AccountFill {
            fill_id: None,
            order_id: None,
            segment_key: "spot".into(),
            instrument_id: "instrument:btc".into(),
            quantity: DecimalValue::new(2, 0),
            price: DecimalValue::new(100, 0),
            side: FillSide::Buy,
            settlement_asset: None,
            settlement_delta: None,
            fee_asset: None,
            fee_amount: None,
            occurred_at_unix_nanos: 99,
        }))],
    }));
    assert!(app.poll_stream_once().unwrap());
    assert_eq!(
        app.query(AccountQuery {
            account_id: "main".into(),
            segments: vec![],
            max_age_seconds: None,
            now_unix_nanos: None
        })
        .unwrap()[0]
            .account
            .state
            .positions["instrument:btc"]
            .quantity,
        DecimalValue::new(2, 0)
    );
}

#[test]
fn fill_settles_balance_and_fee_in_account_application() {
    let source = InMemoryAccountSource {
        snapshots: BTreeMap::from([(
            "spot".into(),
            AccountSnapshot {
                balances: vec![Balance {
                    asset_id: "asset:usdt".into(),
                    asset_code: "USDT".into(),
                    total: DecimalValue::new(1_000_000, 2),
                    ..Default::default()
                }],
                ..empty_snapshot("spot")
            },
        )]),
    };
    let mut app =
        AccountApplication::with_dependencies(vec![segment("spot")], Box::new(source), None)
            .unwrap();
    app.refresh(RefreshAccount {
        account_id: "main".into(),
        segments: vec![],
    })
    .unwrap();
    app.apply_fill(AccountFill {
        fill_id: Some("fill-1".into()),
        order_id: Some("order-1".into()),
        segment_key: "spot".into(),
        instrument_id: "instrument:btc".into(),
        quantity: DecimalValue::new(2, 0),
        price: DecimalValue::new(100, 0),
        side: FillSide::Buy,
        settlement_asset: Some("USDT".into()),
        settlement_delta: Some(DecimalValue::new(-20_000, 2)),
        fee_asset: Some("USDT".into()),
        fee_amount: Some(DecimalValue::new(100, 2)),
        occurred_at_unix_nanos: 10,
    })
    .unwrap();
    let view = &app
        .query(AccountQuery {
            account_id: "main".into(),
            segments: vec![],
            max_age_seconds: None,
            now_unix_nanos: None,
        })
        .unwrap()[0];
    assert_eq!(
        view.account.state.positions["instrument:btc"].quantity,
        DecimalValue::new(2, 0)
    );
    assert_eq!(
        view.account.state.balances["asset:usdt"].total,
        DecimalValue::new(979_900, 2)
    );
}

#[test]
fn duplicate_fill_id_is_rejected_without_mutating_account_state() {
    let mut account = Account::new(segment("spot")).unwrap();
    let fill = AccountFill {
        fill_id: Some("fill-duplicate".into()),
        order_id: None,
        segment_key: "spot".into(),
        instrument_id: "instrument:btc".into(),
        quantity: DecimalValue::new(1, 0),
        price: DecimalValue::new(100, 0),
        side: FillSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: 1,
    };
    account.apply_fill(fill.clone()).unwrap();
    let state_after_first = account.state.clone();
    assert_eq!(
        account.apply_fill(fill),
        Err("fill was already applied".into())
    );
    assert_eq!(account.state, state_after_first);
}

#[test]
fn partial_snapshot_merges_balances_and_removes_zero_positions() {
    let segment = AccountSegment {
        identity: ExternalAccountIdentity::new("fixture", "main").unwrap(),
        segment_key: "spot".into(),
        environment: "live".into(),
        account_model: None,
    };
    let mut account = Account::new(segment).unwrap();
    account
        .apply_snapshot(AccountSnapshot {
            segment_key: "spot".into(),
            balances: vec![Balance {
                asset_id: "asset:usdt".into(),
                asset_code: "USDT".into(),
                total: DecimalValue::new(10, 0),
                ..Default::default()
            }],
            collateral: vec![],
            positions: vec![Position {
                instrument_id: "instrument:btc".into(),
                quantity: DecimalValue::new(1, 0),
                ..Default::default()
            }],
            open_orders: vec![],
            status: AccountStatus::Ready,
            observed_at_unix_nanos: 1,
            equity: None,
            initial_equity: None,
            net_profit: None,
            account_model: None,
            margin_mode: None,
            position_mode: None,
            partial: false,
        })
        .unwrap();
    account
        .apply_snapshot(AccountSnapshot {
            segment_key: "spot".into(),
            balances: vec![Balance {
                asset_id: "asset:usdc".into(),
                asset_code: "USDC".into(),
                total: DecimalValue::new(5, 0),
                ..Default::default()
            }],
            collateral: vec![],
            positions: vec![Position {
                instrument_id: "instrument:btc".into(),
                quantity: DecimalValue::new(0, 0),
                ..Default::default()
            }],
            open_orders: vec![],
            status: AccountStatus::Ready,
            observed_at_unix_nanos: 2,
            equity: None,
            initial_equity: None,
            net_profit: None,
            account_model: None,
            margin_mode: None,
            position_mode: None,
            partial: true,
        })
        .unwrap();
    assert!(account.state.balances.contains_key("asset:usdt"));
    assert!(account.state.balances.contains_key("asset:usdc"));
    assert!(!account.state.positions.contains_key("instrument:btc"));
}

struct TestStream {
    events: Vec<Option<AccountEvent>>,
}

#[test]
fn json_store_restores_account_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("account.json");
    let account = kairos_account::domain::Account::new(segment("spot")).unwrap();
    let mut store = JsonAccountStore::new(&path);
    store.save(&[account]).unwrap();
    let restored = store.load().unwrap();
    assert_eq!(restored.len(), 1);
    assert_eq!(restored[0].0.segment_key, "spot");
}

#[test]
fn order_planning_uses_risk_port_and_releases_on_account_rejection() {
    let source = InMemoryAccountSource {
        snapshots: BTreeMap::from([("spot".into(), empty_snapshot("spot"))]),
    };
    let mut app =
        AccountApplication::with_dependencies(vec![segment("spot")], Box::new(source), None)
            .unwrap();
    let mut risk = RecordingRisk::default();

    let request = OrderRequest {
        order_id: "order-risk-1".into(),
        intent_id: None,
        account_id: "main".into(),
        segment_key: "spot".into(),
        instrument_id: "instrument:btc".into(),
        market_id: None,
        side: OrderSide::Buy,
        quantity: DecimalValue::new(2, 0),
        order_type: OrderType::Market,
        limit_price: None,
    };
    app.plan_order_with_risk(request.clone(), 1, &mut risk)
        .unwrap();
    assert_eq!(risk.reserved, vec!["order-risk-1"]);

    let duplicate = app.plan_order_with_risk(request, 2, &mut risk);
    assert!(duplicate.is_err());
    assert_eq!(risk.released, vec!["order-risk-1"]);
}

#[derive(Default)]
struct RecordingRisk {
    reserved: Vec<String>,
    released: Vec<String>,
}

impl OrderRisk for RecordingRisk {
    fn reserve(&mut self, request: &OrderRiskRequest) -> Result<(), String> {
        self.reserved.push(request.reservation_id.clone());
        Ok(())
    }

    fn release(&mut self, reservation_id: &str) -> Result<(), String> {
        self.released.push(reservation_id.to_string());
        Ok(())
    }

    fn consume(&mut self, _reservation_id: &str) -> Result<(), String> {
        Ok(())
    }
}

impl kairos_account::application::AccountStreamSource for TestStream {
    fn next_event(&mut self) -> Result<Option<AccountEvent>, String> {
        Ok(self.events.pop().flatten())
    }
}
