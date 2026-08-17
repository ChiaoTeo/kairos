use std::collections::BTreeMap;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use kairos_account::composition::registry::{AccountBindingRecord, AccountRegistry};
use kairos_account_contract::decode_account_current;
use kairos_transport::{SharedSnapshotReader, SnapshotEnvelopeMetadata};
use kairos_workspace::Workspace;

struct Server(Child);

impl Server {
    fn stop(mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn start_server(workspace: &Workspace) -> Server {
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
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("start Account server");
    Server(child)
}

fn wait_for_snapshot(
    path: &std::path::Path,
    previous_incarnation: Option<u64>,
) -> (SnapshotEnvelopeMetadata, String) {
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut last_error = String::new();
    while Instant::now() < deadline {
        match SharedSnapshotReader::open(path).and_then(|reader| reader.read_payload()) {
            Ok(frame) => match decode_account_current(&frame.payload) {
                Ok(view)
                    if previous_incarnation
                        .is_none_or(|value| value != frame.producer_incarnation) =>
                {
                    let balance = view.segments().get(0).balances().get(0).total();
                    return (
                        SnapshotEnvelopeMetadata {
                            resource_epoch: frame.resource_epoch,
                            producer_incarnation: frame.producer_incarnation,
                            generation: frame.generation,
                            applied_event_sequence: frame.applied_event_sequence,
                            published_at_unix_nanos: frame.published_at_unix_nanos,
                        },
                        format!("{}:{}", balance.mantissa(), balance.scale()),
                    );
                }
                Ok(_) => last_error = "snapshot still belongs to the previous producer".into(),
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
        .save(workspace.child(&["accounts", "accounts.toml"]).unwrap())
        .unwrap();

    let instance = workspace
        .instance("paper", "restart-test", "instance-1")
        .unwrap();
    let snapshot_path = instance.service_snapshot("account").unwrap();

    let first = start_server(&workspace);
    let (first_metadata, first_balance) = wait_for_snapshot(&snapshot_path, None);
    first.stop();

    let second = start_server(&workspace);
    let (second_metadata, second_balance) =
        wait_for_snapshot(&snapshot_path, Some(first_metadata.producer_incarnation));
    second.stop();

    assert_ne!(
        first_metadata.producer_incarnation,
        second_metadata.producer_incarnation
    );
    assert!(second_metadata.generation >= first_metadata.generation);
    assert_eq!(second_balance, first_balance);
    assert_eq!(second_balance, "1000:0");
}
