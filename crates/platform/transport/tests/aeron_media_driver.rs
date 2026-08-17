use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use kairos_transport::{AeronBytePublisher, AeronByteSubscription, PublishOutcome};

struct Driver(Child);

impl Drop for Driver {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn driver() -> (tempfile::TempDir, Driver) {
    let root = tempfile::tempdir().unwrap();
    let aeron_dir = root.path().join("media");
    let health = root.path().join("ready.json");
    let child = Command::new(env!("CARGO_BIN_EXE_kairos-aeron-driver"))
        .args(["--aeron-dir", aeron_dir.to_str().unwrap(), "--health-file"])
        .arg(&health)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while !health.exists() {
        assert!(
            Instant::now() < deadline,
            "Media Driver readiness timed out"
        );
        thread::sleep(Duration::from_millis(10));
    }
    (root, Driver(child))
}

#[test]
fn live_transport_reports_drop_then_reassembles_large_frame() {
    let (root, _driver) = driver();
    let dir = root.path().join("media");
    let dir = dir.to_str().unwrap();
    let channel = "aeron:ipc";
    let stream_id = 29_001;
    let publisher = AeronBytePublisher::connect(Some(dir), channel, stream_id).unwrap();
    assert_eq!(
        publisher.publish(b"before-subscribe").unwrap(),
        PublishOutcome::DroppedNoSubscriber
    );

    let mut subscription = AeronByteSubscription::connect(Some(dir), channel, stream_id).unwrap();
    let connected = Instant::now() + Duration::from_secs(10);
    while !publisher.has_subscriber().unwrap() {
        assert!(
            Instant::now() < connected,
            "subscription connection timed out"
        );
        subscription.poll(16).unwrap();
        thread::sleep(Duration::from_millis(1));
    }

    let payload = vec![0x5a; 5 * 1024 * 1024];
    assert_eq!(
        publisher.publish(&payload).unwrap(),
        PublishOutcome::Offered
    );
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(frame) = subscription.next_frame().unwrap() {
            assert_eq!(frame, payload);
            break;
        }
        assert!(Instant::now() < deadline, "fragment reassembly timed out");
        thread::sleep(Duration::from_millis(1));
    }
}
