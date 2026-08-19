use std::collections::BTreeMap;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use kairos_account::composition::registry::{AccountBindingRecord, AccountRegistry};
use kairos_account_contract::{
    AccountContractClient, AccountViewKey, AccountViewKind, AccountViewReader, SimulatedSettlement,
};
use kairos_transport::SnapshotEnvelopeMetadata;
use kairos_workspace::Workspace;
use rusteron_media_driver::{AeronDriver, AeronDriverContext, IntoCString};

struct Server {
    child: Child,
    stderr_path: PathBuf,
}

impl Server {
    fn stop(mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }

    fn exit_error(&mut self) -> Option<String> {
        let status = self.child.try_wait().expect("inspect Account server");
        status.map(|status| {
            let stderr = std::fs::read_to_string(&self.stderr_path).unwrap_or_default();
            format!("Account server exited with {status}: {stderr}")
        })
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn start_server(workspace: &Workspace, aeron_dir: &Path) -> Server {
    let stderr_path = workspace
        .root()
        .join(format!("account-server-{}.stderr.log", std::process::id()));
    let stderr = File::create(&stderr_path).expect("create Account server stderr log");
    let child = Command::new(env!("CARGO_BIN_EXE_kairos-account-server"))
        .args([
            "--workspace",
            workspace.root().to_str().unwrap(),
            "--account-id",
            "paper-main",
            "--launch-mode",
            "paper",
            "--launch-id",
            "restart-test",
            "--instance-id",
            "instance-1",
            "--refresh-ms",
            "25",
            "--aeron-dir",
            aeron_dir.to_str().unwrap(),
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::from(stderr))
        .spawn()
        .expect("start Account server");
    Server { child, stderr_path }
}

fn wait_for_snapshot(
    path: &Path,
    server: &mut Server,
    previous_incarnation: Option<u64>,
    minimum_generation: u64,
    expected_balance: &str,
) -> (SnapshotEnvelopeMetadata, String) {
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut last_error = String::new();
    while Instant::now() < deadline {
        if let Some(error) = server.exit_error() {
            panic!("{error}");
        }
        let reader =
            AccountViewKey::new("account:paper-main", "paper-main", AccountViewKind::Current)
                .and_then(|key| AccountViewReader::open(path, key));
        match reader.and_then(|reader| reader.read()) {
            Ok(frame) => match frame.account_current() {
                Ok(view)
                    if previous_incarnation.is_none_or(|value| {
                        value != frame.envelope_metadata().producer_incarnation
                    }) && frame.generation() >= minimum_generation =>
                {
                    let balances = view.segments().get(0).balances();
                    if !balances.is_empty() {
                        let balance = balances.get(0).total();
                        let balance = format!("{}:{}", balance.mantissa(), balance.scale());
                        if balance == expected_balance {
                            return (
                                SnapshotEnvelopeMetadata {
                                    resource_epoch: frame.envelope_metadata().resource_epoch,
                                    producer_incarnation: frame
                                        .envelope_metadata()
                                        .producer_incarnation,
                                    generation: frame.generation(),
                                    applied_event_sequence: frame
                                        .envelope_metadata()
                                        .applied_event_sequence,
                                    published_at_unix_nanos: frame
                                        .envelope_metadata()
                                        .published_at_unix_nanos,
                                },
                                balance,
                            );
                        }
                        last_error = format!(
                            "balance is {balance}, expected {expected_balance}; generation={}",
                            frame.generation()
                        );
                    } else {
                        last_error = "snapshot has no balances".into();
                    }
                },
                Ok(_) => {
                    last_error = format!(
                        "snapshot has not reached the expected incarnation/generation; incarnation={} generation={}",
                        frame.envelope_metadata().producer_incarnation,
                        frame.generation()
                    )
                },
                Err(error) => last_error = error.to_string(),
            },
            Err(error) => last_error = error.to_string(),
        }
        thread::sleep(Duration::from_millis(25));
    }
    panic!("Account snapshot was not published before timeout: {last_error}");
}

#[test]
fn account_server_restart_restores_state_and_republishes_a_new_mmap_incarnation() {
    let directory = tempfile::tempdir().unwrap();
    let workspace = Workspace::init(directory.path(), "account-restart-test").unwrap();
    let aeron_dir = directory.path().join("aeron");
    let driver_context = AeronDriverContext::new().unwrap();
    driver_context
        .set_dir(&aeron_dir.to_string_lossy().into_c_string())
        .unwrap();
    let _driver = AeronDriver::launch_embedded_guard(driver_context, true);
    let registry = AccountRegistry {
        accounts: vec![AccountBindingRecord {
            account_id: "paper-main".into(),
            alias: "paper-main".into(),
            broker: "paper-broker".into(),
            integration_provider: "paper".into(),
            exchange: Some("paper".into()),
            environment: "paper".into(),
            remote_identity: None,
            permissions: BTreeMap::new(),
            segments: vec!["spot".into()],
            segment_products: BTreeMap::from([("spot".into(), "paper".into())]),
            segment_trading_modes: BTreeMap::new(),
            account_model: Some("no_margin".into()),
            credential_id: None,
            credentials: Vec::new(),
            credential_role: None,
            status: "configured".into(),
            initial_balances: vec!["USDT=1000.00".into()],
            fee_rate: None,
            values: BTreeMap::new(),
        }],
    };
    registry
        .save(
            workspace
                .child(&["config", "accounts", "accounts.toml"])
                .unwrap(),
        )
        .unwrap();

    let instance = workspace
        .instance("paper", "restart-test", "instance-1")
        .unwrap();
    let snapshot_path = instance.snapshot(&[]).unwrap();
    let socket_path = instance.socket("account").unwrap();

    let mut first = start_server(&workspace, &aeron_dir);
    let (first_metadata, first_balance) =
        wait_for_snapshot(&snapshot_path, &mut first, None, 1, "1000:0");
    let client = AccountContractClient::connect(&socket_path).unwrap();
    client
        .apply_simulated_settlement(&SimulatedSettlement {
            fill_id: kairos_primitives::FillId::new("restart-persisted-fill").unwrap(),
            order_id: Some(kairos_primitives::OrderId::new("restart-persisted-order").unwrap()),
            segment_key: kairos_primitives::SegmentKey::new("spot").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("paper:BTC-USDT").unwrap(),
            quantity: kairos_primitives::Quantity::new(1, 0).unwrap(),
            price: kairos_primitives::Price::new(100, 0).unwrap(),
            side: kairos_primitives::OrderSide::Buy,
            settlement_asset: Some(kairos_primitives::Currency::new("USDT").unwrap()),
            settlement_delta: Some(kairos_primitives::SignedQuantity::new(-100, 0).unwrap()),
            fee_asset: None,
            fee_amount: None,
            occurred_at_unix_nanos: 1_000_000_000.into(),
        })
        .unwrap();
    let (persisted_metadata, persisted_balance) = wait_for_snapshot(
        &snapshot_path,
        &mut first,
        None,
        first_metadata.generation + 1,
        "900:0",
    );
    first.stop();

    let mut second = start_server(&workspace, &aeron_dir);
    let (second_metadata, second_balance) = wait_for_snapshot(
        &snapshot_path,
        &mut second,
        Some(persisted_metadata.producer_incarnation),
        persisted_metadata.generation,
        "900:0",
    );
    second.stop();

    assert_ne!(
        persisted_metadata.producer_incarnation,
        second_metadata.producer_incarnation
    );
    assert!(second_metadata.generation >= persisted_metadata.generation);
    assert_eq!(first_balance, "1000:0");
    assert_eq!(persisted_balance, "900:0");
    assert_eq!(second_balance, persisted_balance);
}
