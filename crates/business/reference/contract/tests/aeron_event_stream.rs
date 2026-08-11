use std::time::{Duration, Instant};

use kairos_reference_contract::transport::{AeronEventSubscriber, ReferenceAeronEventWriter};
use kairos_reference_contract::{decode_change, LifecycleEvent, ReferenceCatalog};
use rusteron_media_driver::testing::EmbeddedDriver;

#[test]
fn reference_change_is_pushed_and_decoded_over_aeron() {
    let driver = EmbeddedDriver::launch().expect("launch embedded Aeron driver");
    let channel = "aeron:ipc";
    let stream_id = 12_901;
    let mut subscriber = AeronEventSubscriber::connect(
        Some(driver.dir()),
        channel,
        stream_id,
        "reference.lifecycle",
        1,
        "reference-actor",
    )
    .expect("connect Reference subscriber");
    let mut writer = ReferenceAeronEventWriter::connect(
        Some(driver.dir()),
        channel,
        stream_id,
        "reference-actor",
        "reference.lifecycle",
    )
    .expect("connect Reference publisher");
    let events = (1..=64)
        .map(|sequence| LifecycleEvent {
            event_id: format!("reference:{sequence:020}"),
            event_type: "listed".into(),
            event_time_unix_nanos: sequence,
            record_kind: Some("market".into()),
            record_id: Some(format!("market:binance:spot:BTCUSDT:{sequence:020}")),
            market_id: Some(format!("market:binance:spot:BTCUSDT:{sequence:020}")),
            current_status: Some("active".into()),
            ..LifecycleEvent::default()
        })
        .collect::<Vec<_>>();
    let catalog = ReferenceCatalog {
        generation: 1,
        event_sequence: 64,
        lifecycle_events: events.clone(),
        ..ReferenceCatalog::default()
    };

    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        match writer.publish(&catalog, &events) {
            Ok(()) => break,
            Err(error) => {
                assert!(
                    Instant::now() < deadline,
                    "publish Reference event: {error}"
                );
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    }

    let envelope = loop {
        if let Some(envelope) = subscriber.next(1, 1).expect("poll Reference stream") {
            break envelope;
        }
        assert!(
            Instant::now() < deadline,
            "Reference event was not received over Aeron"
        );
        std::thread::sleep(Duration::from_millis(10));
    };
    let change = decode_change(&envelope.payload).expect("decode Reference change");
    assert_eq!(change.generation, 1);
    assert_eq!(change.event_sequence, 64);
    assert_eq!(change.events, events);
}

#[test]
fn reference_change_is_dropped_without_subscriber() {
    let driver = EmbeddedDriver::launch().expect("launch embedded Aeron driver");
    let channel = "aeron:ipc";
    let stream_id = 12_902;
    let mut writer = ReferenceAeronEventWriter::connect(
        Some(driver.dir()),
        channel,
        stream_id,
        "reference-actor",
        "reference.lifecycle",
    )
    .expect("connect Reference publisher");
    let events = vec![LifecycleEvent {
        event_id: "reference:00000000000000000001".into(),
        event_type: "listed".into(),
        event_time_unix_nanos: 1,
        ..LifecycleEvent::default()
    }];
    let catalog = ReferenceCatalog {
        generation: 1,
        event_sequence: 1,
        lifecycle_events: events.clone(),
        ..ReferenceCatalog::default()
    };

    // No subscriber is a normal best-effort state. The publication succeeds
    // from the caller's perspective, but the notification is not retained.
    writer
        .publish(&catalog, &events)
        .expect("drop without subscriber");

    let mut subscriber = AeronEventSubscriber::connect(
        Some(driver.dir()),
        channel,
        stream_id,
        "reference.lifecycle",
        1,
        "reference-actor",
    )
    .expect("connect Reference subscriber");
    std::thread::sleep(Duration::from_millis(50));
    assert!(subscriber
        .next(1, 1)
        .expect("poll Reference stream")
        .is_none());
}
