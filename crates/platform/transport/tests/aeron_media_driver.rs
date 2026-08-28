use std::net::UdpSocket;
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

fn leased_test_channels(count: usize) -> Vec<String> {
    let sockets = (0..count)
        .map(|_| UdpSocket::bind("127.0.0.1:0").unwrap())
        .collect::<Vec<_>>();
    sockets
        .iter()
        .map(|socket| {
            format!(
                "aeron:udp?endpoint=127.0.0.1:{}",
                socket.local_addr().unwrap().port()
            )
        })
        .collect()
}

fn wait_for_frame(subscription: &mut AeronByteSubscription, expected: &[u8]) {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(frame) = subscription.next_frame().unwrap() {
            assert_eq!(frame, expected);
            return;
        }
        assert!(Instant::now() < deadline, "Aeron frame delivery timed out");
        thread::sleep(Duration::from_millis(1));
    }
}

#[test]
fn live_transport_visits_unfragmented_frame_without_entering_owned_queue() {
    let (root, _driver) = driver();
    let dir = root.path().join("media");
    let dir = dir.to_str().unwrap();
    let channel = "aeron:ipc";
    let stream_id = 29_003;
    let publisher = AeronBytePublisher::connect(Some(dir), channel, stream_id).unwrap();
    let mut subscription = AeronByteSubscription::connect(Some(dir), channel, stream_id).unwrap();

    let connected = Instant::now() + Duration::from_secs(10);
    while !publisher.has_subscriber().unwrap() {
        assert!(
            Instant::now() < connected,
            "subscription connection timed out"
        );
        subscription.poll_with(16, |_| {}).unwrap();
        thread::sleep(Duration::from_millis(1));
    }

    let expected = b"callback-scoped-borrow";
    assert_eq!(
        publisher.publish(expected).unwrap(),
        PublishOutcome::Offered
    );
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut visited = false;
    while !visited {
        subscription
            .poll_with(16, |frame| {
                assert_eq!(frame, expected);
                visited = true;
            })
            .unwrap();
        assert!(Instant::now() < deadline, "Aeron frame delivery timed out");
        if !visited {
            thread::sleep(Duration::from_millis(1));
        }
    }

    assert!(subscription.next_frame().unwrap().is_none());
}

#[test]
fn live_routes_fan_out_shared_channel_and_isolate_distinct_channels() {
    let (root, _driver) = driver();
    let dir = root.path().join("media");
    let dir = dir.to_str().unwrap();
    let channels = leased_test_channels(3);
    let stream_id = 29_002;

    let mut workspace_a =
        AeronByteSubscription::connect(Some(dir), &channels[0], stream_id).unwrap();
    let mut workspace_b =
        AeronByteSubscription::connect(Some(dir), &channels[0], stream_id).unwrap();
    let mut instance_a =
        AeronByteSubscription::connect(Some(dir), &channels[1], stream_id).unwrap();
    let mut instance_b =
        AeronByteSubscription::connect(Some(dir), &channels[2], stream_id).unwrap();
    let workspace_publisher =
        AeronBytePublisher::connect(Some(dir), &channels[0], stream_id).unwrap();
    let instance_a_publisher =
        AeronBytePublisher::connect(Some(dir), &channels[1], stream_id).unwrap();

    let connected = Instant::now() + Duration::from_secs(10);
    while !workspace_publisher.has_subscriber().unwrap()
        || !instance_a_publisher.has_subscriber().unwrap()
    {
        assert!(Instant::now() < connected, "route connection timed out");
        workspace_a.poll(16).unwrap();
        workspace_b.poll(16).unwrap();
        instance_a.poll(16).unwrap();
        instance_b.poll(16).unwrap();
        thread::sleep(Duration::from_millis(1));
    }

    let shared = b"workspace-shared";
    assert_eq!(
        workspace_publisher.publish(shared).unwrap(),
        PublishOutcome::Offered
    );
    wait_for_frame(&mut workspace_a, shared);
    wait_for_frame(&mut workspace_b, shared);
    assert!(instance_a.next_frame().unwrap().is_none());
    assert!(instance_b.next_frame().unwrap().is_none());

    let private = b"instance-a-private";
    assert_eq!(
        instance_a_publisher.publish(private).unwrap(),
        PublishOutcome::Offered
    );
    wait_for_frame(&mut instance_a, private);
    assert!(workspace_a.next_frame().unwrap().is_none());
    assert!(workspace_b.next_frame().unwrap().is_none());
    assert!(instance_b.next_frame().unwrap().is_none());
}
