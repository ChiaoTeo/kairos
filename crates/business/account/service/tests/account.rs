use std::collections::BTreeMap;

use kairos_account::composition::account::{
    compose_account_application, compose_account_application_for_segments,
    compose_in_memory_account_application, AccountOptions, AccountRegistry, CredentialRecord,
    CredentialStore,
};
use kairos_account::composition::{empty_snapshot, FlatbuffersAccountPublisher};
use kairos_account::domain::{
    Account, AccountFill, AccountSegment, AccountSnapshot, AccountStatus, ApplyOutcome, AssetId,
    Balance, Decimal, ExternalAccountIdentity, FillId, FillSide, InstrumentId, Position,
    SegmentKey,
};
use kairos_account::{AccountDataQuery, AccountQuery, ReconcileAccount, RefreshAccount};
use kairos_protocol::generated::kairos::account::v_1::root_as_accounts_snapshot;
use kairos_protocol::InstanceIdentity;

fn segment(key: &str) -> AccountSegment {
    AccountSegment {
        identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
        segment_key: SegmentKey::new(key).unwrap(),
        environment: "paper".into(),
        account_model: Some("no_margin".into()),
    }
}

fn balance(asset_id: &str, asset_code: &str, total: Decimal) -> Balance {
    Balance {
        asset_id: AssetId::new(asset_id).unwrap(),
        asset_code: asset_code.into(),
        total,
        available: None,
        locked: None,
        borrowed: None,
        interest: None,
    }
}

fn position(instrument_id: &str, quantity: Decimal) -> Position {
    Position {
        instrument_id: InstrumentId::new(instrument_id).unwrap(),
        market_id: None,
        quantity,
        average_price: None,
        mark_price: None,
        unrealized_pnl: None,
        realized_pnl: None,
        updated_at_unix_nanos: 0,
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
        Decimal::new(1_000_050, 2)
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
    let snapshots = BTreeMap::from([(
        "spot".into(),
        AccountSnapshot {
            segment_key: SegmentKey::new("spot").unwrap(),
            balances: vec![balance("asset:usdt", "USDT", Decimal::new(10_000, 2))],
            collateral: vec![],
            positions: vec![position("instrument:btc", Decimal::new(25, 2))],
            open_orders: vec![],
            status: AccountStatus::Ready,
            observed_at_unix_nanos: 42,
            equity: Some(Decimal::new(10_000, 2)),
            initial_equity: None,
            net_profit: None,
            account_model: None,
            margin_mode: None,
            position_mode: None,
            kind: kairos_account::domain::SnapshotKind::Full,
        },
    )]);
    let mut app =
        compose_in_memory_account_application(vec![segment("spot")], snapshots, None).unwrap();

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
        result[0]
            .balances
            .iter()
            .find(|value| value.asset_id == "asset:usdt")
            .unwrap()
            .total,
        Decimal::new(10_000, 2)
    );
    assert_eq!(
        result[0]
            .positions
            .iter()
            .find(|value| value.instrument_id == "instrument:btc")
            .unwrap()
            .quantity,
        Decimal::new(25, 2)
    );
    app.apply_simulated_fill(AccountFill {
        fill_id: FillId::new("paper-fill-position").unwrap(),
        order_id: None,
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: Decimal::new(1, 2),
        price: Decimal::new(100, 0),
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
    let snapshots = BTreeMap::from([("spot".into(), empty_snapshot("spot"))]);
    let mut app =
        compose_in_memory_account_application(vec![segment("spot")], snapshots, None).unwrap();
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
    let snapshots = BTreeMap::from([("spot".into(), empty_snapshot("spot"))]);
    let mut app =
        compose_in_memory_account_application(vec![segment("spot")], snapshots, None).unwrap();
    app.refresh(RefreshAccount {
        account_id: "main".into(),
        segments: vec![],
    })
    .unwrap();
    app.apply_simulated_fill(AccountFill {
        fill_id: FillId::new("paper-fill-basic").unwrap(),
        order_id: None,
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: Decimal::new(2, 0),
        price: Decimal::new(100, 0),
        side: FillSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: 99,
    })
    .unwrap();
    assert_eq!(
        app.query(AccountQuery {
            account_id: "main".into(),
            segments: vec![],
            max_age_seconds: None,
            now_unix_nanos: None
        })
        .unwrap()[0]
            .positions
            .iter()
            .find(|value| value.instrument_id == "instrument:btc")
            .unwrap()
            .quantity,
        Decimal::new(2, 0)
    );
}

#[test]
fn fill_settles_balance_and_fee_in_account_application() {
    let snapshots = BTreeMap::from([(
        "spot".into(),
        AccountSnapshot {
            balances: vec![balance("asset:usdt", "USDT", Decimal::new(1_000_000, 2))],
            ..empty_snapshot("spot")
        },
    )]);
    let mut app =
        compose_in_memory_account_application(vec![segment("spot")], snapshots, None).unwrap();
    app.refresh(RefreshAccount {
        account_id: "main".into(),
        segments: vec![],
    })
    .unwrap();
    app.apply_simulated_fill(AccountFill {
        fill_id: FillId::new("fill-1").unwrap(),
        order_id: Some("order-1".into()),
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: Decimal::new(2, 0),
        price: Decimal::new(100, 0),
        side: FillSide::Buy,
        settlement_asset: Some("USDT".into()),
        settlement_delta: Some(Decimal::new(-20_000, 2)),
        fee_asset: Some("USDT".into()),
        fee_amount: Some(Decimal::new(100, 2)),
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
        view.positions
            .iter()
            .find(|value| value.instrument_id == "instrument:btc")
            .unwrap()
            .quantity,
        Decimal::new(2, 0)
    );
    assert_eq!(
        view.balances
            .iter()
            .find(|value| value.asset_id == "asset:usdt")
            .unwrap()
            .total,
        Decimal::new(979_900, 2)
    );
}

#[test]
fn duplicate_fill_id_is_rejected_without_mutating_account_state() {
    let mut account = Account::new(segment("spot")).unwrap();
    let fill = AccountFill {
        fill_id: FillId::new("fill-duplicate").unwrap(),
        order_id: None,
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: Decimal::new(1, 0),
        price: Decimal::new(100, 0),
        side: FillSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: 1,
    };
    account.record_fill(fill.clone()).unwrap();
    let state_after_first = account.state().clone();
    assert_eq!(account.record_fill(fill), Ok(ApplyOutcome::Duplicate));
    assert_eq!(account.state(), &state_after_first);
}

#[test]
fn partial_snapshot_merges_balances_and_removes_zero_positions() {
    let segment = AccountSegment {
        identity: ExternalAccountIdentity::new("fixture", "main").unwrap(),
        segment_key: SegmentKey::new("spot").unwrap(),
        environment: "live".into(),
        account_model: None,
    };
    let mut account = Account::new(segment).unwrap();
    account
        .apply_snapshot(AccountSnapshot {
            segment_key: SegmentKey::new("spot").unwrap(),
            balances: vec![balance("asset:usdt", "USDT", Decimal::new(10, 0))],
            collateral: vec![],
            positions: vec![position("instrument:btc", Decimal::new(1, 0))],
            open_orders: vec![],
            status: AccountStatus::Ready,
            observed_at_unix_nanos: 1,
            equity: None,
            initial_equity: None,
            net_profit: None,
            account_model: None,
            margin_mode: None,
            position_mode: None,
            kind: kairos_account::domain::SnapshotKind::Full,
        })
        .unwrap();
    account
        .apply_snapshot(AccountSnapshot {
            segment_key: SegmentKey::new("spot").unwrap(),
            balances: vec![balance("asset:usdc", "USDC", Decimal::new(5, 0))],
            collateral: vec![],
            positions: vec![position("instrument:btc", Decimal::new(0, 0))],
            open_orders: vec![],
            status: AccountStatus::Ready,
            observed_at_unix_nanos: 2,
            equity: None,
            initial_equity: None,
            net_profit: None,
            account_model: None,
            margin_mode: None,
            position_mode: None,
            kind: kairos_account::domain::SnapshotKind::Delta,
        })
        .unwrap();
    assert!(account.state().balances().contains_key("asset:usdt"));
    assert!(account.state().balances().contains_key("asset:usdc"));
    assert!(!account.state().positions().contains_key("instrument:btc"));
}

#[test]
fn json_store_restores_account_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("account.json");
    let mut snapshot = empty_snapshot("spot");
    snapshot.observed_at_unix_nanos = 10;
    snapshot.balances = vec![balance("asset:usdt", "USDT", Decimal::new(42, 0))];
    let mut application = compose_in_memory_account_application(
        vec![segment("spot")],
        BTreeMap::from([("spot".into(), snapshot)]),
        Some(path.clone()),
    )
    .unwrap();
    application
        .refresh(RefreshAccount {
            account_id: "main".into(),
            segments: Vec::new(),
        })
        .unwrap();
    let generation = application.snapshot().generation;

    let restored = compose_in_memory_account_application(
        vec![segment("spot")],
        BTreeMap::new(),
        Some(path.clone()),
    )
    .unwrap();
    assert_eq!(restored.snapshot().generation, generation);
    assert_eq!(
        restored.snapshot().accounts[0].balances[0].total,
        Decimal::new(42, 0)
    );
    let persisted: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    assert_eq!(persisted["schema_version"], 1);
}

#[test]
fn journal_restores_high_frequency_fill_without_checkpoint_rewrite() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("account.json");
    let snapshot = empty_snapshot("spot");
    let mut application = compose_in_memory_account_application(
        vec![segment("spot")],
        BTreeMap::from([("spot".into(), snapshot)]),
        Some(path.clone()),
    )
    .unwrap();
    application
        .refresh(RefreshAccount {
            account_id: "main".into(),
            segments: Vec::new(),
        })
        .unwrap();
    application
        .apply_simulated_fill(AccountFill {
            fill_id: FillId::new("journal-fill-1").unwrap(),
            order_id: None,
            segment_key: SegmentKey::new("spot").unwrap(),
            instrument_id: InstrumentId::new("instrument:btc").unwrap(),
            quantity: Decimal::new(1, 0),
            price: Decimal::new(100, 0),
            side: FillSide::Buy,
            settlement_asset: None,
            settlement_delta: None,
            fee_asset: None,
            fee_amount: None,
            occurred_at_unix_nanos: 11,
        })
        .unwrap();
    let generation = application.snapshot().generation;
    assert!(path.with_extension("events.jsonl").is_file());

    let restored =
        compose_in_memory_account_application(vec![segment("spot")], BTreeMap::new(), Some(path))
            .unwrap();
    assert_eq!(restored.snapshot().generation, generation);
    assert_eq!(
        restored.snapshot().accounts[0].positions[0].quantity,
        Decimal::new(1, 0)
    );
}

#[test]
fn snapshot_transition_rejects_stale_and_duplicate_observations() {
    let mut account = Account::new(segment("spot")).unwrap();
    let mut snapshot = empty_snapshot("spot");
    snapshot.observed_at_unix_nanos = 100;
    snapshot.balances = vec![balance("asset:usdt", "USDT", Decimal::new(10_000, 2))];

    assert_eq!(
        account.apply_snapshot(snapshot.clone()).unwrap(),
        ApplyOutcome::Applied
    );
    let state_after_first = account.state().clone();
    assert_eq!(
        account.apply_snapshot(snapshot.clone()).unwrap(),
        ApplyOutcome::Duplicate
    );

    snapshot.observed_at_unix_nanos = 99;
    snapshot.balances[0].total = Decimal::new(1, 0);
    assert_eq!(
        account.apply_snapshot(snapshot).unwrap(),
        ApplyOutcome::Stale
    );
    assert_eq!(account.state(), &state_after_first);
}

#[test]
fn delta_snapshot_does_not_make_a_stale_account_fresh() {
    let mut account = Account::new(segment("spot")).unwrap();
    let mut full = empty_snapshot("spot");
    full.observed_at_unix_nanos = 100;
    assert_eq!(account.apply_snapshot(full).unwrap(), ApplyOutcome::Applied);
    account.evaluate_staleness(200, 50);
    assert!(account.state().stale());

    let mut delta = empty_snapshot("spot");
    delta.kind = kairos_account::domain::SnapshotKind::Delta;
    delta.observed_at_unix_nanos = 150;
    delta.balances = vec![balance("asset:usdt", "USDT", Decimal::new(5, 0))];
    assert_eq!(
        account.apply_snapshot(delta).unwrap(),
        ApplyOutcome::Applied
    );
    assert!(account.state().stale());
    assert_eq!(account.state().observed_at_unix_nanos(), 100);
}

#[test]
fn reconciliation_transition_is_explicit_and_idempotent() {
    let mut account = Account::new(segment("spot")).unwrap();
    assert_eq!(account.begin_reconciliation(), ApplyOutcome::Applied);
    assert_eq!(account.state().status(), AccountStatus::Reconciling);
    assert!(account.state().stale());
    assert_eq!(account.begin_reconciliation(), ApplyOutcome::NoChange);
}

#[test]
fn observed_live_fill_is_audit_only() {
    let mut account = Account::new(segment("spot")).unwrap();
    let mut snapshot = empty_snapshot("spot");
    snapshot.observed_at_unix_nanos = 10;
    snapshot.balances = vec![balance("asset:usdt", "USDT", Decimal::new(100, 0))];
    snapshot.positions = vec![position("instrument:btc", Decimal::new(1, 0))];
    account.apply_snapshot(snapshot).unwrap();
    let balances_before = account.state().balances().clone();
    let positions_before = account.state().positions().clone();

    assert_eq!(
        account
            .record_fill(AccountFill {
                fill_id: FillId::new("live-fill-1").unwrap(),
                order_id: Some("order-1".into()),
                segment_key: SegmentKey::new("spot").unwrap(),
                instrument_id: InstrumentId::new("instrument:btc").unwrap(),
                quantity: Decimal::new(2, 0),
                price: Decimal::new(25, 0),
                side: FillSide::Buy,
                settlement_asset: Some("USDT".into()),
                settlement_delta: Some(Decimal::new(-50, 0)),
                fee_asset: None,
                fee_amount: None,
                occurred_at_unix_nanos: 20,
            })
            .unwrap(),
        ApplyOutcome::Applied
    );
    assert_eq!(account.state().balances(), &balances_before);
    assert_eq!(account.state().positions(), &positions_before);
}

#[test]
fn persistence_failure_does_not_commit_simulated_fill() {
    let state_path = std::path::PathBuf::from("/dev/null/account-state.json");
    let mut application = compose_in_memory_account_application(
        vec![segment("spot")],
        BTreeMap::new(),
        Some(state_path),
    )
    .unwrap();
    let generation_before = application.snapshot().generation;
    let result = application.apply_simulated_fill(AccountFill {
        fill_id: FillId::new("paper-fill-failure").unwrap(),
        order_id: None,
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: Decimal::new(1, 0),
        price: Decimal::new(100, 0),
        side: FillSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: 1,
    });
    assert!(result.is_err());
    let after = application.snapshot();
    assert_eq!(after.generation, generation_before);
    assert!(after.accounts[0].positions.is_empty());
}
