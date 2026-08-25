use std::collections::BTreeMap;

use kairos_account::composition::account::{
    AccountOptions, AccountSegmentBinding, compose_in_memory_account_application,
    compose_local_account_application_for_segments,
};
use kairos_account::composition::empty_snapshot;
use kairos_account::domain::{
    Account, AccountFill, AccountObservedFill, AccountSegment, AccountSnapshot, AccountStatus,
    ApplyOutcome, AssetId, Balance, EarnHolding, EarnHoldingLiquidity, EarnHoldingState,
    EarnHoldingsSnapshot, ExternalAccountIdentity, FillId, InstrumentId, Money, OrderSide,
    Position, SegmentKey, SignedQuantity, SimulatedCapitalMutation, SimulatedCapitalMutationKind,
};
use kairos_primitives::decimal::{Price, Quantity};

fn order_id(value: &str) -> kairos_primitives::execution::OrderId {
    kairos_primitives::execution::OrderId::new(value).unwrap()
}

fn remote_order_id(value: &str) -> kairos_primitives::integration::RemoteOrderId {
    kairos_primitives::integration::RemoteOrderId::new(value).unwrap()
}

fn currency(value: &str) -> kairos_primitives::reference::Currency {
    kairos_primitives::reference::Currency::new(value).unwrap()
}

fn nanos(value: u64) -> kairos_primitives::time::UnixNanos {
    kairos_primitives::time::UnixNanos::new(value)
}

fn binding(value: &str) -> AccountSegmentBinding {
    AccountSegmentBinding::new(value, value)
}

fn account_id(value: &str) -> kairos_primitives::account::AccountId {
    kairos_primitives::account::AccountId::new(value).unwrap()
}

fn segment_key(value: &str) -> SegmentKey {
    SegmentKey::new(value).unwrap()
}

use kairos_account::application::AccountSegmentView;
use kairos_account::composition::registry::AccountRegistry;
use kairos_account::{
    AccountApplication, AccountCurrentView, AccountRuntimeMode, MarkToMarket, ReconcileAccount,
    RefreshAccount,
};
use kairos_credentials::{CredentialRecord, CredentialStore};

fn segment(key: &str) -> AccountSegment {
    AccountSegment {
        identity: ExternalAccountIdentity::new("binance", "main").unwrap(),
        segment_key: SegmentKey::new(key).unwrap(),
        environment: "paper".into(),
        account_model: Some("no_margin".into()),
    }
}

#[test]
fn simulation_business_time_is_owned_by_the_account_application() {
    let mut application =
        compose_in_memory_account_application(vec![segment("spot")], BTreeMap::new(), None)
            .unwrap();
    assert_eq!(application.runtime_mode(), AccountRuntimeMode::Simulation);

    application.advance_business_time(10).unwrap();
    assert_eq!(application.business_time_unix_nanos(), Some(10));
    assert!(application.advance_business_time(9).is_err());
    assert_eq!(application.business_time_unix_nanos(), Some(10));
}

#[test]
fn simulated_capital_mutation_is_idempotent_persistent_and_updates_earn() {
    let directory = tempfile::tempdir().unwrap();
    let state_path = directory.path().join("account-capital-simulation.json");
    let mutation_id =
        kairos_primitives::runtime::IdempotencyKey::new("capital:subscribe:1").unwrap();
    let mut initial = empty_snapshot("spot");
    initial.observed_at_unix_nanos = nanos(100);
    initial.status = AccountStatus::Ready;
    initial.balances = vec![Balance {
        asset_id: AssetId::new("asset:usdt").unwrap(),
        asset_code: currency("USDT"),
        total: SignedQuantity::new(100, 0).unwrap(),
        available: Some(SignedQuantity::new(100, 0).unwrap()),
        locked: Some(SignedQuantity::ZERO),
        borrowed: None,
        interest: None,
    }];
    let snapshots = BTreeMap::from([("spot".into(), initial.clone())]);
    {
        let mut application = compose_in_memory_account_application(
            vec![segment("spot")],
            snapshots.clone(),
            Some(state_path.clone()),
        )
        .unwrap();
        application
            .refresh(RefreshAccount {
                account_id: account_id("main"),
                segments: Vec::new(),
            })
            .unwrap();
        let mutation = SimulatedCapitalMutation {
            mutation_id: mutation_id.clone(),
            segment_key: segment_key("spot"),
            asset: currency("USDT"),
            amount: Quantity::new(30, 0).unwrap(),
            kind: SimulatedCapitalMutationKind::SubscribeEarn,
            product_id: Some("USDT001".into()),
            occurred_at_unix_nanos: nanos(110),
        };
        application
            .apply_simulated_capital_mutation(mutation.clone())
            .unwrap();
        application
            .apply_simulated_capital_mutation(mutation)
            .unwrap();
        let segment_view = account_segment_view(&application);
        assert_eq!(segment_view.balances[0].total.mantissa(), 70);
        assert_eq!(segment_view.earn_holdings[0].principal.mantissa(), 30);
    }

    let mut recovered =
        compose_in_memory_account_application(vec![segment("spot")], snapshots, Some(state_path))
            .unwrap();
    assert!(
        recovered
            .simulated_capital_mutation_applied(&segment_key("spot"), &mutation_id)
            .unwrap()
    );
    recovered
        .apply_simulated_capital_mutation(SimulatedCapitalMutation {
            mutation_id: kairos_primitives::runtime::IdempotencyKey::new("capital:redeem:1")
                .unwrap(),
            segment_key: segment_key("spot"),
            asset: currency("USDT"),
            amount: Quantity::new(10, 0).unwrap(),
            kind: SimulatedCapitalMutationKind::RedeemEarn,
            product_id: Some("USDT001".into()),
            occurred_at_unix_nanos: nanos(120),
        })
        .unwrap();
    let segment_view = account_segment_view(&recovered);
    assert_eq!(segment_view.balances[0].total.mantissa(), 80);
    assert_eq!(segment_view.earn_holdings[0].principal.mantissa(), 20);
}

#[test]
fn account_rejects_simulated_capital_mutation_until_simulation_mode_is_enabled() {
    let options = AccountOptions {
        provider: "paper".into(),
        product: "spot".into(),
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: String::new(),
        account_id: "paper-main".into(),
        segment: "spot".into(),
        environment: "paper".into(),
        account_model: None,
        initial_balances: vec!["USDT=100".into()],
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
        isolated_margin_symbol: None,
        reference_database: None,
    };
    let mut application =
        compose_local_account_application_for_segments(&options, &[binding("spot")], None)
            .unwrap()
            .application;

    let error = application
        .apply_simulated_capital_mutation(SimulatedCapitalMutation {
            mutation_id: kairos_primitives::runtime::IdempotencyKey::new("capital:disabled:1")
                .unwrap(),
            segment_key: segment_key("spot"),
            asset: currency("USDT"),
            amount: Quantity::new(1, 0).unwrap(),
            kind: SimulatedCapitalMutationKind::DebitLiquid,
            product_id: None,
            occurred_at_unix_nanos: nanos(1),
        })
        .unwrap_err();

    assert!(error.to_string().contains("simulation command is disabled"));
}

#[derive(Default)]
struct CapturingSnapshotPublisher(Option<AccountCurrentView>);

impl CapturingSnapshotPublisher {
    fn publish(&mut self, view: &AccountCurrentView) -> Result<(), String> {
        self.0 = Some(view.clone());
        Ok(())
    }
}

fn account_snapshot(app: &AccountApplication) -> AccountCurrentView {
    let mut publisher = CapturingSnapshotPublisher::default();
    app.publish_current(|view| publisher.publish(view)).unwrap();
    publisher.0.expect("Account published a current snapshot")
}

fn account_segment_view(app: &AccountApplication) -> AccountSegmentView {
    account_snapshot(app)
        .segments
        .into_iter()
        .next()
        .expect("test Account has one configured segment")
}

#[test]
fn one_account_actor_rejects_segments_from_different_account_ids() {
    let mut secondary = segment("margin");
    secondary.identity = ExternalAccountIdentity::new("binance", "secondary").unwrap();
    let error = compose_in_memory_account_application(
        vec![segment("spot"), secondary],
        BTreeMap::new(),
        None,
    )
    .err()
    .expect("mixed account ids must be rejected");

    assert!(error.contains("cannot own multiple account ids"));
}

fn signed(mantissa: i64, scale: u8) -> SignedQuantity {
    SignedQuantity::new(mantissa, scale).unwrap()
}

fn quantity(mantissa: i64, scale: u8) -> Quantity {
    Quantity::new(mantissa, scale).unwrap()
}

fn price(mantissa: i64, scale: u8) -> Price {
    Price::new(mantissa, scale).unwrap()
}

fn money(mantissa: i64, scale: u8) -> Money {
    Money::new(mantissa, scale).unwrap()
}

fn balance(asset_id: &str, asset_code: &str, total: SignedQuantity) -> Balance {
    Balance {
        asset_id: AssetId::new(asset_id).unwrap(),
        asset_code: kairos_primitives::reference::Currency::new(asset_code).unwrap(),
        total,
        available: None,
        locked: None,
        borrowed: None,
        interest: None,
    }
}

fn position(instrument_id: &str, quantity: SignedQuantity) -> Position {
    Position {
        instrument_id: InstrumentId::new(instrument_id).unwrap(),
        market_id: None,
        position_side: kairos_primitives::account::PositionSide::Net,
        quantity,
        average_price: None,
        mark_price: None,
        unrealized_pnl: None,
        realized_pnl: None,
        updated_at_unix_nanos: kairos_primitives::time::UnixNanos::new(0),
    }
}

#[test]
fn credential_uses_only_its_stored_values() {
    let credential = CredentialRecord::new(
        "test-account",
        "binance",
        "readonly",
        [("api_secret".to_owned(), "stored-secret".to_owned())],
    )
    .unwrap();
    assert_eq!(credential.secret_value().as_deref(), Some("stored-secret"));
}

#[test]
fn credential_store_persists_toml_records() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().join("credentials");
    let credential = CredentialRecord::new(
        "binance-live",
        "binance",
        "readonly",
        [
            ("api_key".to_owned(), "stored-key".to_owned()),
            ("api_secret".to_owned(), "stored-secret".to_owned()),
        ],
    )
    .unwrap();
    CredentialStore::put(&root, &credential, false).unwrap();
    let loaded = CredentialStore::load(&root).unwrap();
    assert_eq!(
        loaded.credentials[0].api_key_value().as_deref(),
        Some("stored-key")
    );
    assert!(
        directory
            .path()
            .join("credentials/binance-live.toml")
            .is_file()
    );
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
integration_provider = "binance"
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
provider = "binance"

[credential.values]
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
    let store = CredentialStore::load(&credentials).unwrap();
    assert_eq!(store.credentials[0].credential_id, "binance-read");
    assert_eq!(store.credentials[0].api_key_value().as_deref(), Some("key"));
}

#[test]
fn registry_rejects_implicit_broker_to_provider_routing() {
    let directory = tempfile::tempdir().unwrap();
    let accounts = directory.path().join("accounts");
    std::fs::create_dir_all(&accounts).unwrap();
    std::fs::write(
        accounts.join("main.toml"),
        r#"[account]
id = "main"
broker = "broker-business-identity"
environment = "live"

[segments.spot]
product_family = "spot"
"#,
    )
    .unwrap();

    let error = AccountRegistry::load(accounts.join("accounts.toml")).unwrap_err();
    assert!(error.contains("missing account.integration_provider"));
    assert!(error.contains("provider routing must be explicit"));
}

#[test]
fn paper_account_composition_is_local_and_does_not_require_credentials() {
    let directory = tempfile::tempdir().unwrap();
    let options = AccountOptions {
        provider: "paper".into(),
        product: "spot".into(),
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: "https://api.binance.com".into(),
        account_id: "paper-main".into(),
        segment: "spot".into(),
        environment: "paper".into(),
        account_model: None,
        initial_balances: vec!["USDT=10000.50".into()],
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
        isolated_margin_symbol: None,
        reference_database: None,
    };
    let mut composition = compose_local_account_application_for_segments(
        &options,
        &[binding("spot")],
        Some(directory.path().join("account.json")),
    )
    .unwrap();
    assert_eq!(composition.provider, "paper");
    assert_eq!(
        composition
            .application
            .refresh(RefreshAccount {
                account_id: account_id("paper-main"),
                segments: vec![],
            })
            .unwrap(),
        1
    );
    assert_eq!(account_snapshot(&composition.application).segments.len(), 1);
    assert_eq!(
        account_snapshot(&composition.application).segments[0].balances[0].total,
        signed(1_000_050, 2)
    );
}

#[test]
fn paper_account_composition_restores_multiple_configured_segments() {
    let directory = tempfile::tempdir().unwrap();
    let options = AccountOptions {
        provider: "paper".into(),
        product: "spot".into(),
        api_key: String::new().into(),
        secret: String::new().into(),
        passphrase: String::new().into(),
        base_url: String::new(),
        account_id: "paper-main".into(),
        segment: "spot".into(),
        environment: "paper".into(),
        account_model: None,
        initial_balances: Vec::new(),
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
        isolated_margin_symbol: None,
        reference_database: None,
    };
    let mut composition = compose_local_account_application_for_segments(
        &options,
        &[binding("spot"), binding("margin")],
        Some(directory.path().join("account.json")),
    )
    .unwrap();
    composition
        .application
        .refresh(RefreshAccount {
            account_id: account_id("paper-main"),
            segments: vec![],
        })
        .unwrap();
    assert_eq!(account_snapshot(&composition.application).segments.len(), 2);
}

#[test]
fn refresh_owns_segment_state_and_query_returns_typed_view() {
    let snapshots = BTreeMap::from([(
        "spot".into(),
        AccountSnapshot {
            segment_key: SegmentKey::new("spot").unwrap(),
            balances: vec![balance("asset:usdt", "USDT", signed(10_000, 2))],
            collateral: vec![],
            positions: vec![position("instrument:btc", signed(25, 2))],
            open_orders: vec![],
            status: AccountStatus::Ready,
            observed_at_unix_nanos: 42.into(),
            equity: Some(money(10_000, 2)),
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
            account_id: account_id("main"),
            segments: vec![]
        })
        .unwrap(),
        1
    );
    let result = account_snapshot(&app).segments;
    assert_eq!(result.len(), 1);
    assert_eq!(
        result[0]
            .balances
            .iter()
            .find(|value| value.asset_id == "asset:usdt")
            .unwrap()
            .total,
        signed(10_000, 2)
    );
    assert_eq!(
        result[0]
            .positions
            .iter()
            .find(|value| value.instrument_id == "instrument:btc")
            .unwrap()
            .quantity,
        signed(25, 2)
    );
    app.apply_simulated_fill(AccountFill {
        fill_id: FillId::new("paper-fill-position").unwrap(),
        order_id: None,
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: quantity(1, 2),
        price: price(100, 0),
        side: OrderSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: nanos(43),
    })
    .unwrap();
    let reconciliation = app
        .reconcile_report(ReconcileAccount {
            account_id: account_id("main"),
            segments: vec![],
        })
        .unwrap();
    assert!(
        reconciliation
            .differences
            .iter()
            .any(|value| value.field == "position.quantity" && value.key == "instrument:btc:net")
    );
    let segment_view = account_segment_view(&app);
    assert_eq!(segment_view.balances[0].asset_code, "USDT");
    assert_eq!(segment_view.positions.len(), 1);
}

#[test]
fn fill_event_updates_account_position_owned_by_actor() {
    let snapshots = BTreeMap::from([("spot".into(), empty_snapshot("spot"))]);
    let mut app =
        compose_in_memory_account_application(vec![segment("spot")], snapshots, None).unwrap();
    app.refresh(RefreshAccount {
        account_id: account_id("main"),
        segments: vec![],
    })
    .unwrap();
    app.apply_simulated_fill(AccountFill {
        fill_id: FillId::new("paper-fill-basic").unwrap(),
        order_id: None,
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: quantity(2, 0),
        price: price(100, 0),
        side: OrderSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: nanos(99),
    })
    .unwrap();
    assert_eq!(
        account_segment_view(&app)
            .positions
            .iter()
            .find(|value| value.instrument_id == "instrument:btc")
            .unwrap()
            .quantity,
        signed(2, 0)
    );
}

#[test]
fn fill_settles_balance_and_fee_in_account_application() {
    let snapshots = BTreeMap::from([(
        "spot".into(),
        AccountSnapshot {
            balances: vec![balance("asset:usdt", "USDT", signed(1_000_000, 2))],
            ..empty_snapshot("spot")
        },
    )]);
    let mut app =
        compose_in_memory_account_application(vec![segment("spot")], snapshots, None).unwrap();
    app.refresh(RefreshAccount {
        account_id: account_id("main"),
        segments: vec![],
    })
    .unwrap();
    app.apply_simulated_fill(AccountFill {
        fill_id: FillId::new("fill-1").unwrap(),
        order_id: Some(order_id("order-1")),
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: quantity(2, 0),
        price: price(100, 0),
        side: OrderSide::Buy,
        settlement_asset: Some(currency("USDT")),
        settlement_delta: Some(signed(-20_000, 2)),
        fee_asset: Some(currency("USDT")),
        fee_amount: Some(signed(100, 2)),
        occurred_at_unix_nanos: nanos(10),
    })
    .unwrap();
    let view = account_segment_view(&app);
    assert_eq!(
        view.positions
            .iter()
            .find(|value| value.instrument_id == "instrument:btc")
            .unwrap()
            .quantity,
        signed(2, 0)
    );
    assert_eq!(
        view.balances
            .iter()
            .find(|value| value.asset_id == "asset:usdt")
            .unwrap()
            .total,
        signed(979_900, 2)
    );
}

#[test]
fn simulated_settlement_tracks_average_cost_and_realized_pnl() {
    let snapshots = BTreeMap::from([(
        "spot".into(),
        AccountSnapshot {
            balances: vec![balance("asset:usdt", "USDT", signed(1_000_000, 2))],
            ..empty_snapshot("spot")
        },
    )]);
    let mut app =
        compose_in_memory_account_application(vec![segment("spot")], snapshots, None).unwrap();
    app.refresh(RefreshAccount {
        account_id: account_id("main"),
        segments: vec![],
    })
    .unwrap();

    for (id, quantity, price, at) in [
        ("buy-1", quantity(2, 0), price(100, 0), 10),
        ("buy-2", quantity(2, 0), price(120, 0), 20),
        ("sell-1", quantity(1, 0), price(130, 0), 30),
    ] {
        app.apply_simulated_fill(AccountFill {
            fill_id: FillId::new(id).unwrap(),
            order_id: Some(order_id(id)),
            segment_key: SegmentKey::new("spot").unwrap(),
            instrument_id: InstrumentId::new("instrument:btc").unwrap(),
            quantity,
            price,
            side: if id.starts_with("buy") {
                OrderSide::Buy
            } else {
                OrderSide::Sell
            },
            settlement_asset: None,
            settlement_delta: None,
            fee_asset: None,
            fee_amount: None,
            occurred_at_unix_nanos: nanos(at),
        })
        .unwrap();
    }

    let view = account_segment_view(&app);
    let position = &view.positions[0];
    assert_eq!(position.quantity, signed(3, 0));
    assert_eq!(position.average_price, Some(price(110, 0)));
    assert_eq!(position.realized_pnl, Some(money(20, 0)));
}

#[test]
fn mark_to_market_updates_equity_and_unrealized_pnl() {
    let mut initial = empty_snapshot("spot");
    initial.balances = vec![balance("asset:usdt", "USDT", signed(10_000, 0))];
    initial.equity = Some(money(10_000, 0));
    initial.initial_equity = Some(money(10_000, 0));
    let snapshots = BTreeMap::from([("spot".into(), initial)]);
    let mut app =
        compose_in_memory_account_application(vec![segment("spot")], snapshots, None).unwrap();
    app.refresh(RefreshAccount {
        account_id: account_id("main"),
        segments: vec![],
    })
    .unwrap();
    app.apply_simulated_fill(AccountFill {
        fill_id: FillId::new("mark-fill").unwrap(),
        order_id: Some(order_id("mark-order")),
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: quantity(2, 0),
        price: price(100, 0),
        side: OrderSide::Buy,
        settlement_asset: Some(currency("USDT")),
        settlement_delta: Some(signed(-200, 0)),
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: nanos(10),
    })
    .unwrap();
    app.mark_to_market(MarkToMarket {
        segment_key: segment_key("spot"),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quote_asset: currency("USDT"),
        mark_price: price(120, 0),
        observed_at_unix_nanos: nanos(20),
    })
    .unwrap();
    let view = account_segment_view(&app);
    assert_eq!(view.equity, Some(money(10_040, 0)));
    assert_eq!(view.net_profit, Some(money(40, 0)));
    assert_eq!(view.positions[0].unrealized_pnl, Some(money(40, 0)));
}

#[test]
fn duplicate_fill_id_is_rejected_without_mutating_account_state() {
    let mut account = Account::new(segment("spot")).unwrap();
    let fill = AccountFill {
        fill_id: FillId::new("fill-duplicate").unwrap(),
        order_id: None,
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: quantity(1, 0),
        price: price(100, 0),
        side: OrderSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: nanos(1),
    };
    account.record_fill(fill.clone()).unwrap();
    let state_after_first = account.state().clone();
    assert_eq!(account.record_fill(fill), Ok(ApplyOutcome::Duplicate));
    assert_eq!(account.state(), &state_after_first);
}

#[test]
fn conflicting_duplicate_fill_enters_reconciliation() {
    let mut account = Account::new(segment("spot")).unwrap();
    let fill = AccountFill {
        fill_id: FillId::new("fill-conflict").unwrap(),
        order_id: None,
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: quantity(1, 0),
        price: price(100, 0),
        side: OrderSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: nanos(1),
    };
    account.record_fill(fill.clone()).unwrap();
    let mut conflict = fill;
    conflict.price = price(101, 0);
    assert_eq!(account.record_fill(conflict), Ok(ApplyOutcome::Conflict));
    assert_eq!(account.status(), AccountStatus::Reconciling);
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
            balances: vec![balance("asset:usdt", "USDT", signed(10, 0))],
            collateral: vec![],
            positions: vec![position("instrument:btc", signed(1, 0))],
            open_orders: vec![],
            status: AccountStatus::Ready,
            observed_at_unix_nanos: 1.into(),
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
            balances: vec![balance("asset:usdc", "USDC", signed(5, 0))],
            collateral: vec![],
            positions: vec![position("instrument:btc", signed(0, 0))],
            open_orders: vec![],
            status: AccountStatus::Ready,
            observed_at_unix_nanos: 2.into(),
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
    assert!(!account.state().positions().contains_key(&(
        InstrumentId::new("instrument:btc").unwrap(),
        kairos_primitives::account::PositionSide::Net,
    )));
}

#[test]
fn hedge_mode_positions_keep_long_and_short_as_distinct_facts() {
    let segment = AccountSegment {
        identity: ExternalAccountIdentity::new("fixture", "main").unwrap(),
        segment_key: SegmentKey::new("usd_m_futures").unwrap(),
        environment: "live".into(),
        account_model: None,
    };
    let mut account = Account::new(segment).unwrap();
    let mut long = position("instrument:btc-perp", signed(2, 0));
    long.position_side = kairos_primitives::account::PositionSide::Long;
    let mut short = position("instrument:btc-perp", signed(-1, 0));
    short.position_side = kairos_primitives::account::PositionSide::Short;
    account
        .apply_snapshot(AccountSnapshot {
            segment_key: SegmentKey::new("usd_m_futures").unwrap(),
            positions: vec![long.clone(), short.clone()],
            observed_at_unix_nanos: 1.into(),
            kind: kairos_account::domain::SnapshotKind::Full,
            ..empty_snapshot("usd_m_futures")
        })
        .unwrap();

    assert_eq!(account.state().positions().len(), 2);
    assert!(account.state().positions().contains_key(&(
        InstrumentId::new("instrument:btc-perp").unwrap(),
        kairos_primitives::account::PositionSide::Long,
    )));
    assert!(account.state().positions().contains_key(&(
        InstrumentId::new("instrument:btc-perp").unwrap(),
        kairos_primitives::account::PositionSide::Short,
    )));

    long.quantity = signed(0, 0);
    account
        .apply_snapshot(AccountSnapshot {
            segment_key: SegmentKey::new("usd_m_futures").unwrap(),
            positions: vec![long],
            observed_at_unix_nanos: 2.into(),
            kind: kairos_account::domain::SnapshotKind::Delta,
            ..empty_snapshot("usd_m_futures")
        })
        .unwrap();

    assert_eq!(account.state().positions().len(), 1);
    assert!(account.state().positions().contains_key(&(
        InstrumentId::new("instrument:btc-perp").unwrap(),
        kairos_primitives::account::PositionSide::Short,
    )));
}

#[test]
fn json_store_restores_account_state() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("account.json");
    let mut snapshot = empty_snapshot("spot");
    snapshot.observed_at_unix_nanos = 10.into();
    snapshot.balances = vec![balance("asset:usdt", "USDT", signed(42, 0))];
    let mut application = compose_in_memory_account_application(
        vec![segment("spot")],
        BTreeMap::from([("spot".into(), snapshot)]),
        Some(path.clone()),
    )
    .unwrap();
    application
        .refresh(RefreshAccount {
            account_id: account_id("main"),
            segments: Vec::new(),
        })
        .unwrap();
    let generation = account_snapshot(&application).generation;

    let restored = compose_in_memory_account_application(
        vec![segment("spot")],
        BTreeMap::new(),
        Some(path.clone()),
    )
    .unwrap();
    assert_eq!(account_snapshot(&restored).generation, generation);
    assert_eq!(
        account_snapshot(&restored).segments[0].balances[0].total,
        signed(42, 0)
    );
    let persisted: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    assert_eq!(persisted["schema_version"], 2);
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
            account_id: account_id("main"),
            segments: Vec::new(),
        })
        .unwrap();
    application
        .apply_simulated_fill(AccountFill {
            fill_id: FillId::new("journal-fill-1").unwrap(),
            order_id: None,
            segment_key: SegmentKey::new("spot").unwrap(),
            instrument_id: InstrumentId::new("instrument:btc").unwrap(),
            quantity: quantity(1, 0),
            price: price(100, 0),
            side: OrderSide::Buy,
            settlement_asset: None,
            settlement_delta: None,
            fee_asset: None,
            fee_amount: None,
            occurred_at_unix_nanos: nanos(11),
        })
        .unwrap();
    let generation = account_snapshot(&application).generation;
    assert!(path.with_extension("events.jsonl").is_file());

    let restored =
        compose_in_memory_account_application(vec![segment("spot")], BTreeMap::new(), Some(path))
            .unwrap();
    assert_eq!(account_snapshot(&restored).generation, generation);
    assert_eq!(
        account_snapshot(&restored).segments[0].positions[0].quantity,
        signed(1, 0)
    );
}

#[test]
fn snapshot_transition_rejects_stale_and_duplicate_observations() {
    let mut account = Account::new(segment("spot")).unwrap();
    let mut snapshot = empty_snapshot("spot");
    snapshot.observed_at_unix_nanos = 100.into();
    snapshot.balances = vec![balance("asset:usdt", "USDT", signed(10_000, 2))];

    assert_eq!(
        account.apply_snapshot(snapshot.clone()).unwrap(),
        ApplyOutcome::Applied
    );
    let state_after_first = account.state().clone();
    assert_eq!(
        account.apply_snapshot(snapshot.clone()).unwrap(),
        ApplyOutcome::Duplicate
    );

    snapshot.observed_at_unix_nanos = 99.into();
    snapshot.balances[0].total = signed(1, 0);
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
    full.observed_at_unix_nanos = 100.into();
    assert_eq!(account.apply_snapshot(full).unwrap(), ApplyOutcome::Applied);
    account.evaluate_staleness(
        kairos_primitives::time::UnixNanos::new(200),
        kairos_primitives::time::DurationNanos::new(50),
    );
    assert!(account.state().stale());

    let mut delta = empty_snapshot("spot");
    delta.kind = kairos_account::domain::SnapshotKind::Delta;
    delta.observed_at_unix_nanos = 150.into();
    delta.balances = vec![balance("asset:usdt", "USDT", signed(5, 0))];
    assert_eq!(
        account.apply_snapshot(delta).unwrap(),
        ApplyOutcome::Applied
    );
    assert!(account.state().stale());
    assert_eq!(
        account.state().observed_at_unix_nanos(),
        kairos_primitives::time::UnixNanos::new(100)
    );
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
    snapshot.observed_at_unix_nanos = 10.into();
    snapshot.balances = vec![balance("asset:usdt", "USDT", signed(100, 0))];
    snapshot.positions = vec![position("instrument:btc", signed(1, 0))];
    account.apply_snapshot(snapshot).unwrap();
    let balances_before = account.state().balances().clone();
    let positions_before = account.state().positions().clone();

    assert_eq!(
        account
            .record_fill(AccountFill {
                fill_id: FillId::new("live-fill-1").unwrap(),
                order_id: Some(order_id("order-1")),
                segment_key: SegmentKey::new("spot").unwrap(),
                instrument_id: InstrumentId::new("instrument:btc").unwrap(),
                quantity: quantity(2, 0),
                price: price(25, 0),
                side: OrderSide::Buy,
                settlement_asset: Some(currency("USDT")),
                settlement_delta: Some(signed(-50, 0)),
                fee_asset: None,
                fee_amount: None,
                occurred_at_unix_nanos: nanos(20),
            })
            .unwrap(),
        ApplyOutcome::Applied
    );
    assert_eq!(account.state().balances(), &balances_before);
    assert_eq!(account.state().positions(), &positions_before);
}

#[test]
fn account_first_observed_fill_enters_reconciliation_without_settlement() {
    let mut account = Account::new(segment("spot")).unwrap();
    let mut snapshot = empty_snapshot("spot");
    snapshot.observed_at_unix_nanos = 10.into();
    snapshot.balances = vec![balance("asset:usdt", "USDT", signed(100, 0))];
    account.apply_snapshot(snapshot).unwrap();
    let balances_before = account.state().balances().clone();

    assert_eq!(
        account
            .observe_fill(AccountObservedFill {
                fill_id: FillId::new("account-first-fill").unwrap(),
                order_id: Some(order_id("local-order-not-yet-recovered")),
                remote_order_id: Some(remote_order_id("exchange-order-1")),
                segment_key: SegmentKey::new("spot").unwrap(),
                instrument_id: InstrumentId::new("instrument:btc").unwrap(),
                quantity: quantity(1, 0),
                price: price(100, 0),
                side: OrderSide::Buy,
                occurred_at_unix_nanos: nanos(20),
            })
            .unwrap(),
        ApplyOutcome::Applied
    );
    assert_eq!(account.status(), AccountStatus::Reconciling);
    assert_eq!(account.state().balances(), &balances_before);
    assert_eq!(account.observed_fills().len(), 1);
    assert_eq!(
        account
            .record_fill(AccountFill {
                fill_id: FillId::new("account-first-fill").unwrap(),
                order_id: Some(order_id("local-order-not-yet-recovered")),
                segment_key: SegmentKey::new("spot").unwrap(),
                instrument_id: InstrumentId::new("instrument:btc").unwrap(),
                quantity: quantity(1, 0),
                price: price(100, 0),
                side: OrderSide::Buy,
                settlement_asset: None,
                settlement_delta: None,
                fee_asset: None,
                fee_amount: None,
                occurred_at_unix_nanos: nanos(20),
            })
            .unwrap(),
        ApplyOutcome::Applied
    );
    assert!(account.observed_fills().is_empty());
    assert_eq!(
        account
            .observe_fill(AccountObservedFill {
                fill_id: FillId::new("account-first-fill").unwrap(),
                order_id: Some(order_id("local-order-not-yet-recovered")),
                remote_order_id: Some(remote_order_id("exchange-order-1")),
                segment_key: SegmentKey::new("spot").unwrap(),
                instrument_id: InstrumentId::new("instrument:btc").unwrap(),
                quantity: quantity(1, 0),
                price: price(100, 0),
                side: OrderSide::Buy,
                occurred_at_unix_nanos: nanos(20),
            })
            .unwrap(),
        ApplyOutcome::Duplicate
    );
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
    let generation_before = account_snapshot(&application).generation;
    let result = application.apply_simulated_fill(AccountFill {
        fill_id: FillId::new("paper-fill-failure").unwrap(),
        order_id: None,
        segment_key: SegmentKey::new("spot").unwrap(),
        instrument_id: InstrumentId::new("instrument:btc").unwrap(),
        quantity: quantity(1, 0),
        price: price(100, 0),
        side: OrderSide::Buy,
        settlement_asset: None,
        settlement_delta: None,
        fee_asset: None,
        fee_amount: None,
        occurred_at_unix_nanos: nanos(1),
    });
    assert!(result.is_err());
    let after = account_snapshot(&application);
    assert_eq!(after.generation, generation_before);
    assert!(after.segments[0].positions.is_empty());
}

#[test]
fn earn_holdings_have_an_independent_watermark_and_fact_set() {
    let mut account = Account::new(segment("funding")).unwrap();
    let holding = EarnHolding {
        participant_position_id: Some("position-1".into()),
        product_id: "USDT001".into(),
        asset: currency("USDT"),
        principal: quantity(100, 0),
        redeemable: Some(quantity(80, 0)),
        accrued_rewards: Vec::new(),
        liquidity: EarnHoldingLiquidity::Immediate,
        state: EarnHoldingState::Active,
        observed_at_unix_nanos: nanos(10),
    };
    assert_eq!(
        account
            .apply_earn_snapshot(EarnHoldingsSnapshot {
                segment_key: segment_key("funding"),
                holdings: vec![holding],
                observed_at_unix_nanos: nanos(10),
                complete: true,
            })
            .unwrap(),
        ApplyOutcome::Applied
    );
    assert_eq!(account.state().earn_holdings().len(), 1);
    assert_eq!(account.state().earn_watermark_unix_nanos(), nanos(10));

    // A normal balance snapshot does not own or clear the Earn fact set.
    account
        .apply_snapshot(AccountSnapshot {
            segment_key: segment_key("funding"),
            balances: Vec::new(),
            collateral: Vec::new(),
            positions: Vec::new(),
            open_orders: Vec::new(),
            status: AccountStatus::Ready,
            observed_at_unix_nanos: nanos(11),
            equity: None,
            initial_equity: None,
            net_profit: None,
            account_model: None,
            margin_mode: None,
            position_mode: None,
            kind: kairos_account::domain::SnapshotKind::Full,
        })
        .unwrap();
    assert_eq!(account.state().earn_holdings().len(), 1);

    assert_eq!(
        account
            .apply_earn_snapshot(EarnHoldingsSnapshot {
                segment_key: segment_key("funding"),
                holdings: Vec::new(),
                observed_at_unix_nanos: nanos(9),
                complete: true,
            })
            .unwrap(),
        ApplyOutcome::Stale
    );
}
